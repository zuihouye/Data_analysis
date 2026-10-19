"""锁 + 状态机的回归测试。

两段互相独立：
  * 锁的测试只需要 Redis，本机没起 Redis 就整组 skip（照抄 test_import_db.py 的风格）；
  * 状态机测试需要 MySQL，用临时库 taobao_user_test，连不上同样 skip。

跑法：在项目根目录 `pytest tests/test_lock.py -v`
"""
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text

from config import Config
from utils.lock import LockBusyError, acquire, order_lock, release, renew
from utils.redis_client import get_redis, reset_redis

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / 'schema.sql'
TEST_DB = 'taobao_user_test'


def _redis_ok():
    try:
        get_redis().ping()
        return True
    except Exception:
        return False


def _uri(database=''):
    auth = f'{Config.DB_USER}:{quote_plus(Config.DB_PASSWORD)}'
    return (f'mysql+pymysql://{auth}@{Config.DB_HOST}:{Config.DB_PORT}'
            f'/{database}?charset=utf8mb4')


def _mysql_ok():
    """连服务器级（不带库名）来探测。

    ⚠️ 别写成 `_uri(TEST_DB)`。TEST_DB 是测试自己现建的，一开始并不存在，
    那样写会永远报 (1049, "Unknown database 'taobao_user_test'")，
    导致状态机那条测试在每台机器上都被**静默 skip** —— 看起来"跑过了"，
    其实一次都没跑（这是 2026-10-19 实际踩到的坑）。
    """
    try:
        eng = create_engine(_uri())
        with eng.connect() as c:
            c.execute(text('SELECT 1'))
        eng.dispose()
        return True
    except Exception:
        return False


needs_redis = pytest.mark.skipif(not _redis_ok(), reason='本机没起 Redis，跳过锁测试')
needs_mysql = pytest.mark.skipif(not _mysql_ok(), reason='连不上 MySQL，跳过状态机测试')


@pytest.fixture
def rds():
    reset_redis()
    client = get_redis()
    client.flushdb()                 # 测试专用 db，flush 是安全的
    yield client
    client.flushdb()
    reset_redis()


# --------------------------------------------------------------- 锁
@needs_redis
def test_同一把锁只能被抢到一次(rds):
    assert acquire('t:lock', ttl_ms=2000) is not None
    assert acquire('t:lock', ttl_ms=2000, wait_ms=0) is None


@needs_redis
def test_锁带TTL不会永久死锁(rds):
    acquire('t:lock', ttl_ms=2000)
    assert 0 < rds.pttl('t:lock') <= 2000


@needs_redis
def test_释放后别人能拿到(rds):
    token = acquire('t:lock', ttl_ms=2000)
    assert release('t:lock', token) is True
    assert acquire('t:lock', ttl_ms=2000) is not None


@needs_redis
def test_不能删掉别人的锁(rds):
    token = acquire('t:lock', ttl_ms=2000)
    assert release('t:lock', 'not-my-token') is False
    assert rds.get('t:lock') == token


@needs_redis
def test_锁过期被接管后老持有者既不能删也不能续(rds):
    old = acquire('t:lock', ttl_ms=2000)
    rds.delete('t:lock')                     # 等价于 TTL 到期
    new = acquire('t:lock', ttl_ms=2000)
    assert release('t:lock', old) is False
    assert renew('t:lock', old, 2000) is False
    assert rds.get('t:lock') == new
    assert renew('t:lock', new, 2000) is True


@needs_redis
def test_抢不到锁抛LockBusyError而不是静默继续(rds):
    rds.set('lock:pay:order:9', 'someone-else', nx=True, px=5000)
    with pytest.raises(LockBusyError):
        with order_lock(9, wait_ms=0):
            raise AssertionError('不该进到这里')
    assert rds.get('lock:pay:order:9') == 'someone-else'   # 别人的锁不能被误删


# --------------------------------------------------------------- 状态机
@needs_mysql
def test_条件UPDATE的rowcount能识别重复操作():
    """这才是"钱不出错"的那道保险：靠 rowcount 判定，而不是先查后改。"""
    stmts = [s.strip() for s in '\n'.join(
        ln for ln in SCHEMA_SQL.read_text(encoding='utf-8').splitlines()
        if not ln.strip().startswith('--')).split(';') if s.strip()]

    admin = create_engine(_uri())
    with admin.begin() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS {TEST_DB}'))
        c.execute(text(f'CREATE DATABASE {TEST_DB} DEFAULT CHARACTER SET utf8mb4'))
    admin.dispose()

    eng = create_engine(_uri(TEST_DB))
    with eng.begin() as c:
        for s in stmts:
            c.execute(text(s))
        c.execute(text("INSERT INTO customers (customer_id, region) VALUES ('C1','贵州')"))
        c.execute(text("INSERT INTO products (product_id, product_name, product_category, ppu)"
                       " VALUES ('P1','测试','数码',199.00)"))
        c.execute(text("INSERT INTO orders (transaction_id, order_date, customer_id, product_id,"
                       " quantity, ppu, amount, order_status)"
                       " VALUES ('T1', CURDATE(), 'C1', 'P1', 1, 199.00, 199.00, 1)"))

    with eng.begin() as c:
        n1 = c.execute(text('UPDATE orders SET order_status = 3'
                            ' WHERE order_id = 1 AND order_status = 1')).rowcount
        n2 = c.execute(text('UPDATE orders SET order_status = 3'
                            ' WHERE order_id = 1 AND order_status = 1')).rowcount
    assert n1 == 1, '第一次应该真的改到 1 行'
    assert n2 == 0, '第二次应该改到 0 行 —— 这就是"状态已被别人改过"的判据'
    eng.dispose()

    with admin.begin() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS {TEST_DB}'))
    admin.dispose()