"""Redis 分布式锁。

要点（每条都是真踩过的坑）：

1. 抢锁必须 `SET key token NX PX ttl` 一条原子命令。
   写成 `if not rds.exists(k): rds.set(k, ...)` 就有窗口，两个请求会同时"自认为抢到了锁"。

2. 放锁必须用 Lua 校验 token。直接 `DEL` 会出现：
   A 抢到锁 -> A 卡住超过 TTL -> 锁自动过期 -> B 抢到锁 -> A 醒来 DEL
   结果把 B 的锁删了，C 又能进来，同一订单再次并发。

3. 抢不到不是立刻失败，而是在 wait_ms 内自旋重试。
   实测：轮询间隔 50ms 时，实际进入临界区的延迟在 300ms 上下（调度颗粒度就在这个量级），
   所以 wait_ms 别设得太小气（默认 3 秒是合适的）。

4. TTL 取"正常业务耗时的 3~5 倍"：太短业务没跑完锁就没了，太长进程被 kill 后要等很久。
"""

import logging
import time
import uuid
from contextlib import contextmanager

from config import Config
from utils.redis_client import get_redis

logger = logging.getLogger(__name__)

# KEYS[1]=锁的 key, ARGV[1]=持有者 token
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 续期同样要校验 token，否则会替别人续命
_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    -- 如果一致，说明是自己持有的，执行毫秒级续期
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""

# 注册一次即可：register_script 只在本机算 SHA1，不发网络请求。
# 之后调用走 EVALSHA，只发 40 位 SHA1，不再重复传脚本体。
# 脚本缓存失效（Redis 重启 / SCRIPT FLUSH / 内存淘汰）时 redis-py 会自己
# 捕获 NoScriptError -> SCRIPT LOAD -> 重试，所以不用手写 evalsha + 回退逻辑。
_release_script = get_redis().register_script(_RELEASE_LUA)
_renew_script = get_redis().register_script(_RENEW_LUA)


class LockBusyError(RuntimeError):
    """等不到锁。调用方应回 409 + 让用户重试，而不是 500。"""

def acquire(key, ttl_ms, wait_ms=0, interval_ms=50):
    """抢锁成功返回 token；在 wait_ms 内等不到返回 None（不抛异常）。"""
    rds = get_redis()
    token = uuid.uuid4().hex          # 每次抢锁都必须换新 token
    deadline = time.monotonic() + wait_ms / 1000.0
    while True:
        # nx=True 保证"不存在才写入"；px 保证"进程异常退出也能自动过期"
        if rds.set(key, token, nx=True, px=ttl_ms):
            logger.info('抢锁成功 key=%s token=%s ttl=%sms', key, token[:8], ttl_ms)
            return token
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval_ms / 1000.0)

def release(key, token):
    """放锁。token 不对就什么都不做（说明锁已过期且被别人接管了）。"""
    # 显式传 client：reset_redis() 之后要拿到当前单例，不能用注册时的旧 client
    released = _release_script(keys=[key], args=[token], client=get_redis())
    if not released:
        logger.warning('放锁时 token 不匹配（锁可能已过期被别人接管）key=%s', key)
    return bool(released)

def renew(key,token,ttl_ms):
    """续期。返回 False 表示锁已经不属于你了 —— 必须立刻停止业务，不能再写库。"""
    return bool(_renew_script(keys=[key], args=[token, ttl_ms], client=get_redis()))

@contextmanager #  with中抢锁-返回token-安全放锁
def redis_lock(key, ttl_ms, wait_ms=0, interval_ms=50):
    token = acquire(key, ttl_ms=ttl_ms, wait_ms=wait_ms, interval_ms=interval_ms)
    if token is None:
        raise LockBusyError(f'操作处理中，请稍后重试（key={key}）')
    try:
        yield token
    finally:
        release(key, token)     # 异常也会放锁，否则要等 TTL 到期才解锁

@contextmanager #
def order_lock(order_id, ttl_ms=None, wait_ms=None):
    """订单维度的锁：支付 / 取消 / 退款都走它。"""
    key = f'lock:pay:order:{order_id}'
    with redis_lock(key,
                    ttl_ms=ttl_ms or Config.PAY_LOCK_TTL_MS,
                    wait_ms=wait_ms or Config.PAY_LOCK_WAIT_MS) as token:
        yield token
