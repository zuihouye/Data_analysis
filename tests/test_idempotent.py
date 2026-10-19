"""幂等装饰器的回归测试。

只依赖 Redis：本机没起 Redis 就整组 skip（和 test_lock.py / test_import_db.py 一个风格）。
用独立的 Flask app 起几条假接口，不碰项目真实的视图和数据库。

针对于 2026-10-19 讨论出来的两个坑（都属于"静默串味"）：
  1. 2xx 但不是 JSON 的响应：`Response.get_json()` 返回 None 而不抛异常，
     旧实现会把它存成 "null"，重放时静默返回一个完全不同的响应；
  2. 重放状态码：缓存条件是 2xx，旧实现重放时硬编码 200，201/204 会被改写。

跑法：在项目根目录 `pytest tests/test_idempotent.py -v`
"""
import json

import pytest
from flask import Flask, Response, jsonify

from utils.idempotent import idempotent
from utils.redis_client import get_redis

PREFIX = 'unittest'


def _redis_ok():
    try:
        get_redis().ping()
        return True
    except Exception:
        return False


needs_redis = pytest.mark.skipif(not _redis_ok(), reason='本机没起 Redis，跳过幂等测试')


@pytest.fixture
def app():
    """每条测试一个干净的 app，calls 用来数"业务真的执行了几次"。"""
    app = Flask(__name__)
    calls = {'json': 0, 'text': 0, 'created': 0, 'broken': 0}

    @app.route('/json')
    @idempotent(PREFIX)
    def json_route():
        calls['json'] += 1
        return jsonify(ok=True, n=calls['json'])

    @app.route('/text')
    @idempotent(PREFIX)
    def text_route():
        calls['text'] += 1
        return Response('纯文本 OK', mimetype='text/plain')

    @app.route('/created')
    @idempotent(PREFIX)
    def created_route():
        calls['created'] += 1
        return jsonify(ok=True, order_id=21991), 201

    @app.route('/broken')
    @idempotent(PREFIX)
    def broken_route():
        calls['broken'] += 1
        return Response('{不是合法 JSON', mimetype='application/json')

    app.calls = calls
    return app


@pytest.fixture
def rds():
    """只用测试前缀做清理，不 flushdb（免得把开发时的 key 一起干掉）。"""
    client = get_redis()

    def clean():
        for k in client.scan_iter(match=f'idem:{PREFIX}:*'):
            client.delete(k)

    clean()
    yield client
    clean()


@needs_redis
def test_JSON的2xx会写缓存且重放不重复执行业务(app, rds):
    c = app.test_client()
    headers = {'X-Request-Id': 'json-1'}

    first = c.get('/json', headers=headers)
    second = c.get('/json', headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json() == {'ok': True, 'n': 1}
    assert app.calls['json'] == 1, '第二次应该命中缓存，业务不能再执行一次'

    stored = json.loads(rds.get(f'idem:{PREFIX}:json-1'))
    assert stored == {'__idem__': 1, 'status': 200, 'json': {'ok': True, 'n': 1}}


@needs_redis
def test_非JSON的2xx不写缓存(app, rds):
    """坑 1 的回归：非 JSON 的 2xx 必须跳过缓存，不能被存成 "null"。"""
    c = app.test_client()
    headers = {'X-Request-Id': 'text-1'}

    first = c.get('/text', headers=headers)
    second = c.get('/text', headers=headers)

    assert first.get_data(as_text=True) == second.get_data(as_text=True) == '纯文本 OK'
    assert app.calls['text'] == 2, '非 JSON 响应不该被缓存，第二次必须真的再执行一次'
    assert rds.get(f'idem:{PREFIX}:text-1') is None


@needs_redis
def test_重放保留原来的状态码(app, rds):
    """坑 2 的回归：201 不能被重放成 200。"""
    c = app.test_client()
    headers = {'X-Request-Id': 'created-1'}

    assert c.get('/created', headers=headers).status_code == 201
    assert c.get('/created', headers=headers).status_code == 201, '重放不能把 201 改成 200'
    assert app.calls['created'] == 1

    stored = json.loads(rds.get(f'idem:{PREFIX}:created-1'))
    assert stored['status'] == 201


@needs_redis
def test_声明是JSON但响应体损坏时也跳过缓存(app, rds):
    """比坑 1 更隐蔽的一种：Content-Type 是对了，但 body 解析不了。"""
    c = app.test_client()
    headers = {'X-Request-Id': 'broken-1'}

    for _ in range(2):
        r = c.get('/broken', headers=headers)
        assert r.status_code == 200
        assert r.get_data(as_text=True) == '{不是合法 JSON'

    assert app.calls['broken'] == 2
    assert rds.get(f'idem:{PREFIX}:broken-1') is None


@needs_redis
def test_缓存内容损坏时按未命中处理而不是500(app, rds):
    """Redis 里的值被人手工改坏 / 版本不兼容时，宁可多跑一次业务也不要 500。"""
    rds.set(f'idem:{PREFIX}:bad-1', '这不是 JSON', px=60000)

    r = app.test_client().get('/json', headers={'X-Request-Id': 'bad-1'})

    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'n': 1}
    assert app.calls['json'] == 1


@needs_redis
def test_旧格式的裸payload仍能重放(app, rds):
    """兼容改造前写进 Redis 的值（那时只存裸 payload，重放固定 200）。"""
    rds.set(f'idem:{PREFIX}:legacy-1', json.dumps({'ok': True, 'legacy': True}), px=60000)

    r = app.test_client().get('/json', headers={'X-Request-Id': 'legacy-1'})

    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'legacy': True}
    assert app.calls['json'] == 0, '命中旧格式缓存时也不该执行业务'


def test_缺request_id直接400且不碰Redis(app):
    r = app.test_client().get('/json')
    assert r.status_code == 400
    assert r.get_json()['code'] == 'MISSING_REQUEST_ID'
    assert app.calls['json'] == 0
