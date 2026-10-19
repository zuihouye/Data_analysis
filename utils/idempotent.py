"""幂等：同一个 request_id 的请求，业务体只执行一次。

池子里存的是完整响应 JSON —— 用户重试时拿到的应该是跟第一次**一模一样**的响应，
而不是"操作太频繁，请稍后再试"这种让人再点一次的提示。

只缓存 2xx：这一点是实测纠正出来的。第一版写的是"非 5xx 就缓存"，
结果 409 LOCK_BUSY 被缓存住，用户第一次撞上锁之后，用同一个 request_id
重试全部命中那个 409，1 小时内永久卡死。失败必须允许重试。

2026-10-19 又补了两条（都属于"静默串味"，比直接报错更危险）：
  1. **只缓存 Content-Type 是 JSON 且能解析成功的 2xx**。`Response.get_json()` 对非 JSON
     响应返回 None 而**不抛异常**（实测 Flask 3.1.3），旧实现会把它 `json.dumps(None)`
     存成 `"null"`，重放时用户拿到一个语义完全不同的响应。
  2. **状态码随缓存一起存**（信封 `{"__idem__":1,"status":…,"json":…}`）。旧实现重放时
     硬编码 200，而缓存条件是 2xx —— 201/204 会被悄悄改写成 200。
     旧格式（裸 payload）仍然兼容读取，过了最长一个 TTL 就可以删掉那段兼容分支。
"""
import functools
import hashlib
import json
import logging

from flask import jsonify, request

from config import Config
from utils.redis_client import get_redis

logger = logging.getLogger(__name__)

REQ_ID_HEADER = 'X-Request-Id'

def get_request_id(required=True):
    """优先级：请求头 > JSON 字段 > 表单字段。

    刻意不留"没有就自动生成"的退路：自动生成等于每次重试都是新 id，
    幂等形同虚设。拿不到就报 400，让调用方补齐。
    """
    rid = request.headers.get(REQ_ID_HEADER)
    if not rid:
        payload = request.get_json(silent=True) or {}
        rid = payload.get('request_id') or request.form.get('request_id')
    if not rid and required:
        raise ValueError(f'缺少 {REQ_ID_HEADER} 请求头，无法做幂等校验')
    return rid


def _cache_key(prefix, request_id):
    # 超长 request_id 先哈希，避免把 key 撑爆 / 破坏 key 规范
    if len(request_id) > 64:
        request_id = hashlib.sha1(request_id.encode()).hexdigest()
    return f'idem:{prefix}:{request_id}'


def _dump(status_code, payload):
    """缓存信封：状态码和 JSON 一起存，重放时才能原样返回（201 不能变成 200）。"""
    return json.dumps({'__idem__': 1, 'status': status_code, 'json': payload})


def _load(cached):
    """把缓存值还原成 (status_code, payload)。

    兼容改造前的旧格式 —— 那时只存裸 payload、重放固定 200。
    旧值最多只会活一个 PAY_IDEMPOTENT_TTL（默认 24h），过期后这段兼容可以删。
    """
    data = json.loads(cached)
    if isinstance(data, dict) and data.get('__idem__') == 1:
        return int(data['status']), data['json']
    return 200, data


def idempotent(prefix, ttl_seconds=None):
    """装饰接口函数。只缓存 2xx 且是合法 JSON 的结果。"""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                rid = get_request_id()
            except ValueError as e:
                return jsonify(ok=False, code='MISSING_REQUEST_ID', msg=str(e)), 400

            key = _cache_key(prefix, rid)

            rds = get_redis()
            cached = rds.get(key)
            if cached:
                try:
                    status_code, payload = _load(cached)
                except (ValueError, KeyError, TypeError):
                    # 缓存内容坏了：按"未命中"处理。宁可多执行一次业务，也不要给用户 500。
                    logger.warning('幂等缓存内容损坏，按未命中处理 key=%s', key, exc_info=True)
                else:
                    logger.info('幂等命中，直接返回上次结果 key=%s status=%s', key, status_code)
                    return jsonify(payload), status_code

            result = fn(*args, **kwargs)  # 执行支付等操作
            body, status = result if isinstance(result, tuple) else (result, 200)

            if 200 <= status < 300:
                if not body.is_json:
                    # 非 JSON 的 2xx 直接跳过缓存：body.get_json() 对非 JSON 返回 None
                    # 而不报错，存进去就是 "null"，重放时用户拿到的是另一个响应。
                    logger.warning('幂等层不缓存非 JSON 响应：status=%s content_type=%s',
                                   status, body.content_type)
                else:
                    try:
                        payload = json.loads(body.get_data(as_text=True))
                    except ValueError:
                        logger.warning('幂等层不缓存：声明是 JSON 但响应体解析失败', exc_info=True)
                    else:
                        rds.set(key, _dump(status, payload),
                                px=(ttl_seconds or Config.PAY_IDEMPOTENT_TTL) * 1000)
            return body, status
        return wrapper
    return decorator
