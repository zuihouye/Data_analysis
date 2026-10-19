"""Redis 连接（进程内惰性单例）。

刻意不写成模块级 `rds = Redis(...)`：那样 import 的瞬间就会去连 Redis，
测试收集阶段、或者只想跑 RFM 分析的时候都会因为 Redis 没起而报错。
惰性单例把"连接"推迟到第一次真用到锁的时候。
"""
import logging
import threading
from redis import Redis
from config import Config

logger = logging.getLogger(__name__)
_client = None
_lock = threading.Lock()

def get_redis()->Redis:
    # 惰性连接。双重检查：拿到锁后要再判一次，否则两个线程会各建一个连接池
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = Redis(**Config.redis_kwargs())
                logger.info('Redis 连接池已创建: %s:%s db=%s',
                            Config.REDIS_HOST, Config.REDIS_PORT, Config.REDIS_DB)
    return _client

def reset_redis():
    """测试用：丢掉当前连接，下次调用重建。"""
    global _client
    with _lock:
        if _client:
            _client.close()

        _client = None