# P1：支付接口 + Redis 抢锁（含完整代码）

> 目标：给项目加上"电商支付"这条链路的地基 —— **接口骨架 + 分布式锁 + 幂等**。
> 本阶段**不接真实支付网关、不建流水表、不改 `orders` 表结构**，那部分是 P1-B 待办
> （见第 11 节）。真实支付一旦接上就涉及回调验签、金额对账、重复退款，不适合和地基一起做。
>
> 对照 `E:\Flask_app\docs\01-改造路线图.md`。改动 **7 个文件**（新建 4 个），代码全部在下面。

---

## 速查图（面试前一晚只看这一页）

**三层防线一句话**：**锁管「并发」，幂等管「重投」，状态机管「最终正确」**。
Redis 只负责加速和串行化，**最终正确性落在数据库**。

```text
前端：X-Request-Id（幂等小票） + X-CSRF-Token（安全）
   │
   ▼
①  CSRF 钩子（P0-2 安全层）           不带 token → 400 CSRF 校验失败
   ▼
②  @idempotent 查缓存                 命中 → 直接返回上次那份 2xx（不碰锁、不碰库）
   │   key = idem:{prefix}:{request_id}
   ▼
③  order_lock 抢锁                    SET lock:pay:order:{id} <uuid4> NX PX 10000
   │   抢不到：3s 内 50ms 自旋          → 409 LOCK_BUSY
   ▼
④  db.engine.begin() 事务
   ├─ SELECT ... FOR UPDATE           ← 行锁
   ├─ 状态校验                         已支付 / 已关闭 → 409
   └─ 条件 UPDATE + rowcount 判定       rowcount=0 → 409 STATE_CHANGED ← 最终防线
   ▲ 出事务 = 提交 + 释放行锁
   ▼
⑤  网络 IO（调网关）—— 必须在锁外      P1-B 待办，本阶段直接 501
   ▲
   │  finally：放锁（EVALSHA + Lua 校验 token）
   ▼
⑥  @idempotent 写缓存                 仅 2xx；PX = PAY_IDEMPOTENT_TTL×1000 = 86400s
```

**每一步归谁**

| 归属 | 粒度 / key | 代码 | 一句话理由 |
|---|---|---|---|
| 安全层（P0-2） | 会话 cookie | `views/auth.py` | 有会话 cookie 的 POST 就必须防 CSRF |
| **幂等** | 请求：`idem:{prefix}:{request_id}` | `utils/idempotent.py` | 重试要拿到**同一份**响应，而不是"操作太频繁" |
| **Redis 锁** | 订单：`lock:pay:order:{order_id}` | `utils/lock.py` | 两个**不同**小票打同一订单也要串行 |
| **数据库状态机** | 行：`orders.order_status` | `views/pay.py` | Redis 会过期、会丢，钱不能靠它保证 |

**必背的 6 个常量**

| 项 | 值 | 备注 |
|---|---|---|
| 锁 TTL | `PAY_LOCK_TTL_MS = 10000` | 取正常业务耗时的 3~5 倍 |
| 抢锁等待 | `PAY_LOCK_WAIT_MS = 3000` | 50ms 自旋；实测进临界区延迟约 300ms |
| 幂等 TTL | `PAY_IDEMPOTENT_TTL = 86400` | 只缓存 2xx |
| 锁 key | `lock:pay:order:{order_id}` | 订单维度 |
| 幂等 key | `idem:{prefix}:{request_id}` | prefix = `prepay` / `cancel` |
| Redis DB | `REDIS_DB = 1` | 与其它用途隔离 |

**失败码速查（接口契约）**

| 场景 | 状态码 | code |
|---|---|---|
| 缺 CSRF token | 400 | （纯文本：`CSRF 校验失败`） |
| 缺 `X-Request-Id` | 400 | `MISSING_REQUEST_ID` |
| 抢不到锁 | 409 | `LOCK_BUSY` |
| 订单已支付 / 已关闭 | 409 | `ALREADY_PAID` / `ORDER_CLOSED` |
| 状态在别处被改了 | 409 | `STATE_CHANGED` |
| 网关未接 | 501 | `PAY_NOT_IMPLEMENTED` |

**三个「为什么」（面试必答）**

1. **为什么用 `SET NX PX` 而不是 `exists` + `set`**：后者两条命令之间有窗口，两个请求会同时"自认为抢到锁"。
2. **为什么放锁要用 Lua 校验 token**：A 卡住超 TTL → 锁过期 → B 抢到 → A 醒来 `DEL` 会把 B 的锁删掉。
3. **为什么用 `register_script`（EVALSHA）**：省掉每次传脚本体；脚本缓存失效时 redis-py 自动 `SCRIPT LOAD` 重试（见 6.1）。

**已知短板（主动说，别等人问）**：同一 request_id 并发时，写缓存在放锁之后 → 存在重复执行的窗口，
现在靠条件 UPDATE 兜底；彻底堵法（占位式幂等 / request_id 维度的锁）见 14.3。

**章节导航**：设计决策 → 第 1 节；代码 → 第 2~10 节；本阶段没做什么（P1-B）→ 第 11 节；
真机验收记录 → 第 12 节；你要执行的命令 → 第 13 节；**每步归属的完整时序 → 第 14 节**。

---

## 0. 改动清单

| # | 文件 | 动作 |
|---|---|---|
| 1 | `requirements.txt` | 加一行 `redis`（版本建议见第 3 节） |
| 2 | `.env` / `.env.example` | 新增 Redis 5 个配置项 |
| 3 | `config.py` | **追加** Redis 配置 + `redis_kwargs()`（其余不动） |
| 4 | `utils/redis_client.py` | **新建**：惰性单例连接 |
| 5 | `utils/lock.py` | **新建**：抢锁 / 放锁 / 续期 / `order_lock` |
| 6 | `utils/idempotent.py` | **新建**：`@idempotent` 装饰器 |
| 7 | `views/pay.py` | **新建**：3 个接口（查询 / 发起支付 / 取消） |
| 8 | `views/__init__.py` | 加 2 行：导入并注册 `pay` 蓝图 |
| 9 | `tests/test_lock.py` | **新建**：锁 + 状态机的回归测试（7 条） |
| 10 | `tests/test_idempotent.py` | **新建**：幂等装饰器的回归测试（7 条，含坑 1 / 坑 2） |

> 测试文件官方是拆两个的：`test_lock.py` 管锁和"条件 UPDATE 的 rowcount"状态机，
> `test_idempotent.py` 管 `@idempotent`。两份都只需 Redis（状态机那条额外要 MySQL），
> 连不上就整组 skip。落地后 `pytest -q` → **55 passed**（41 + 7 + 7）。

**`schema.sql` 本阶段一行都不用改** —— `orders.order_status` 现有语义
（`1:已下单 / 2:已支付 / 3:已取消`）正好就是状态机要用的三个状态。

---

## 1. 六个设计决策（先看这段，决定要不要照抄）

1. **锁的粒度是"订单"，不是"用户"。**
   `lock:pay:order:{order_id}`。锁用户会误伤"同一个人并发支付两笔不同订单"，
   而真正要防的重复提交，永远是针对**同一笔订单**。

2. **抢锁一条原子命令，放锁必须用 Lua 校验 token。**
   抢：`SET key token NX PX ttl`。绝不能写成 `if not exists(key): set(key)` ——
   中间有窗口，两个请求会同时以为自己抢到了。
   放：Lua 里比对 token 再 `DEL`。如果直接 `DEL`，会出现"锁过期被 B 接管、
   A 醒来把 B 的锁删了"的经典事故。

3. **`request_id` 必须由调用方提供，不许服务端自动生成。**
   自动生成等于每次重试都是新 id，幂等形同虚设。拿不到就回 400。

4. **Redis 用独立的 db 索引（默认 1）。**
   跟业务缓存分开：万一有人对着 db0 执行 `FLUSHDB`，不会连正在生效的支付锁和幂等记录一起冲掉。

5. **幂等只缓存 2xx 结果。**
   这条是实测纠正出来的：第一版写的是"非 5xx 就缓存"，结果 `409 LOCK_BUSY` 被缓存住 ——
   用户第一次撞上锁，之后用同一个 `request_id` 重试全部命中那个 409，**1 小时内永久卡死**。
   501 / 409 / 400 这类失败必须允许重试。

6. **本阶段 `prepay` 明确返回 501，而不是"假装成功"。**
   接口契约、锁、幂等、状态校验全是真的，唯独"调网关"没实现。
   这样前端可以先联调，也绝不会出现"订单被错误推进成已支付"。

---

## 2. 步骤 1：`.env` / `.env.example`（各追加 5 行）

```ini
# Redis：只给支付锁和幂等用。刻意用独立的 db（1），跟业务缓存分开。
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=1
REDIS_PASSWORD=

# 锁参数（一般不用改）
# PAY_LOCK_TTL_MS=10000      # 锁最长持有 10 秒，超了自动释放
# PAY_LOCK_WAIT_MS=3000      # 抢不到时最多等 3 秒，等不到回 409
# PAY_IDEMPOTENT_TTL=86400   # 幂等结果保留 24 小时
```

> 你的 `E:\Flask_app\.env` 里目前没有这几项，本机 Redis 是**带密码**的，必须填 `REDIS_PASSWORD`。

---

## 3. 步骤 2：`requirements.txt`（加一行）

```text
redis==6.4.0
```

> **版本说明（2026-10-19 重新实测，坑很多，务必照抄版本号）**：本机 Redis 是 **3.0.504**（2015 年）。
>
> - ✅ **`redis==6.4.0` 实测可用**：Python 3.14（`data_analysis`）下 `PING` 正常，
>   `register_script` / EVALSHA / NOSCRIPT 回退全部验证通过（见第 12 节）。
> - ❌ **不要裸 `pip install redis`**：会装到 8.x，连 Redis 3.0.504 时**握手阶段**就报
>   `ResponseError: unknown command 'HELLO'`（新版要谈 RESP3，老服务器不认）。
> - ❌ **`redis==3.5.3` 在 Python 3.12+ 根本 import 不进去**：它 `from distutils.version import StrictVersion`，
>   而 `distutils` 在 3.12 已被移除（`flask_py` 是 3.10 所以能跑，`data_analysis` 是 3.14 所以不行）。
> - 结论：**必须把 `redis==6.4.0` 锁进 `requirements.txt`**；除非同时升级 Redis，否则别升到 7.x/8.x。

---

## 4. 步骤 3：`config.py`（在 `Config` 类里追加）

```python
    # ---------------------------------------------------------- Redis
    # 只给支付锁 / 幂等用。用独立 db 索引，跟业务缓存分开：
    # 万一有人对 db0 执行 FLUSHDB，不会把正在生效的支付锁一起冲掉。
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB   = int(os.getenv('REDIS_DB', '1'))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')

    # 锁参数集中放这里，测试可以覆盖
    PAY_LOCK_TTL_MS      = int(os.getenv('PAY_LOCK_TTL_MS', '10000'))
    PAY_LOCK_WAIT_MS     = int(os.getenv('PAY_LOCK_WAIT_MS', '3000'))
    PAY_IDEMPOTENT_TTL   = int(os.getenv('PAY_IDEMPOTENT_TTL', '86400'))

    @classmethod
    def redis_kwargs(cls):
        return dict(host=cls.REDIS_HOST, port=cls.REDIS_PORT,
                    db=cls.REDIS_DB, password=cls.REDIS_PASSWORD or None,
                    decode_responses=True,        # 必须 True，否则 Lua 返回 bytes，token 比较会全不相等
                    socket_connect_timeout=1,     # 支付链路里 Redis 挂了要"快速失败"，不能挂住请求
                    socket_timeout=1,
                    health_check_interval=30)
```

---

## 5. 步骤 4：`utils/redis_client.py`（新建，完整文件）

```python
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


def get_redis() -> Redis:
    """惰性单例。redis-py 自带连接池，多线程共用一个实例即可。"""
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
        if _client is not None:
            _client.close()
        _client = None
```

---

## 6. 步骤 5：`utils/lock.py`（新建，完整文件）

```python
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


def renew(key, token, ttl_ms):
    """续期。返回 False 表示锁已经不属于你了 —— 必须立刻停止业务，不能再写库。"""
    return bool(_renew_script(keys=[key], args=[token, ttl_ms], client=get_redis()))


@contextmanager
def redis_lock(key, ttl_ms, wait_ms=0, interval_ms=50):
    token = acquire(key, ttl_ms=ttl_ms, wait_ms=wait_ms, interval_ms=interval_ms)
    if token is None:
        raise LockBusyError(f'操作处理中，请稍后重试（key={key}）')
    try:
        yield token
    finally:
        release(key, token)      # 异常也会放锁，否则要等 TTL 到期才解锁


@contextmanager
def order_lock(order_id, ttl_ms=None, wait_ms=None):
    """订单维度的锁：支付 / 取消 / 退款都走它。"""
    key = f'lock:pay:order:{order_id}'
    with redis_lock(key,
                    ttl_ms=ttl_ms or Config.PAY_LOCK_TTL_MS,
                    wait_ms=wait_ms or Config.PAY_LOCK_WAIT_MS) as token:
        yield token
```

> **不要用 `flask.g` 存 Redis 客户端给锁用**：`g` 是请求级、单线程的。
> 这里用模块级单例，跟请求上下文完全解耦。
> 需要"业务可能跑超 TTL"时再加看门狗：后台线程每 `TTL/3` 调一次 `renew()`，
> 返回 `False` 就立刻停业务、且不能再写库。

### 6.1 生产环境：为什么用 `register_script`（EVALSHA）而不是每次 `eval`

先给结论：**两种写法功能等价，放锁的正确性来自 Lua 本身；差别只在网络传输和规范**。

- `eval(lua, 1, key, token)` 每次都把整段脚本体发给 Redis；`register_script` 返回的
  `Script` 对象走 `EVALSHA`，只发 40 位 SHA1。本项目锁脚本只有 4 行，收益很小；
  脚本上百行时才明显。
- **回退是自动的，别自己手写 `evalsha`**。Redis 重启、`SCRIPT FLUSH`、Redis 7 起的内存
  淘汰都会让脚本缓存失效，此时 EVALSHA 报 `NOSCRIPT`。redis-py 的 `Script.__call__`
  内部已经处理好了（本机 redis-py 3.5.3 与 8.1.0 源码一致）：
  ```python
  try:
      return client.evalsha(self.sha, len(keys), *args)
  except NoScriptError:
      self.sha = client.script_load(self.script)      # 缓存没了就重新装载
      return client.evalsha(self.sha, len(keys), *args)
  ```
- **注册一次、调用时显式传 `client`**。`utils/redis_client.py` 是惰性单例且有
  `reset_redis()` 给测试用；`register_script` 会把 client 记在 `Script` 对象上，
  所以调用写 `client=get_redis()`，`reset_redis()` 之后不会用到已经关闭的旧连接。
- **面试说法**：「放锁的原子性由 Lua 保证，EVAL 还是 EVALSHA 只影响每次多传的字节数；
  我选 EVALSHA，并依赖客户端自带的 NOSCRIPT 回退，而不是自己写重试逻辑。」
- 多实例 / 集群：脚本要注册到每个节点（`RedisCluster` 场景）；本项目是单机 Redis，不需要。

---

## 7. 步骤 6：`utils/idempotent.py`（新建，完整文件）

> 2026-10-19 更新：这一节已按"两个坑"加固过（下面代码即最终版，仓库里的文件与它一致）。
> - **坑 1**：`Response.get_json()` 对非 JSON 响应返回 `None` 而**不抛异常**（Flask 3.1.3 实测），
>   旧写法 `json.dumps(body.get_json())` 会把它存成 `"null"`，重放时用户拿到一个语义完全不同的响应。
>   → 现在只缓存 `body.is_json` 且能 `json.loads` 成功的响应，否则打 warning 跳过缓存。
> - **坑 2**：缓存条件是 2xx，但重放时硬编码 `200`，所以 201/204 会被悄悄改写成 200。
>   → 现在把状态码一起存进信封 `{"__idem__":1,"status":…,"json":…}`，重放原样返回；
>   旧格式（裸 payload）仍能读，最多活一个 TTL，之后可删兼容分支。
> - 顺带把"缓存内容损坏"也兜住了：解析失败按**未命中**处理，宁可多跑一次业务也不给用户 500。

```python
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
```

---

## 8. 步骤 7：`views/pay.py`（新建，完整文件）

```python
"""支付相关接口。

三条不变量（改这个文件前先读一遍）：
  1. 任何"读取订单状态并根据它做决定"的操作，都必须先拿 order_lock(order_id)；
  2. 状态判断 + 状态修改必须在同一个事务里，且用条件 UPDATE 的 rowcount 判定结果，
     不允许写成「先 if 查出来的状态，再无条件 UPDATE」；
  3. 调第三方网关（网络 IO）必须放在事务之外，绝不放在持有行锁的 with 块里。

当前阶段：只落地"锁 + 幂等 + 状态机"骨架，不接真实支付网关。
真网关 / 回调 / 流水表见第 11 节 P1-B 待办。
"""
import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from utils import db
from utils.idempotent import idempotent
from utils.lock import LockBusyError, order_lock

logger = logging.getLogger(__name__)

pay_blue = Blueprint('pay', __name__, url_prefix='/pay')

# 订单状态，与 schema.sql 注释保持一致：1 已下单 / 2 已支付 / 3 已取消
ORDER_UNPAID, ORDER_PAID, ORDER_CANCELLED = 1, 2, 3


def _fetch_order(conn, order_id, for_update=False):
    """读订单。for_update=True 时加行锁（MySQL 上是 SELECT ... FOR UPDATE）。"""
    sql = ('SELECT order_id, transaction_id, amount, order_status '
           'FROM orders WHERE order_id = :oid')
    if for_update:
        sql += ' FOR UPDATE'
    return conn.execute(text(sql), {'oid': order_id}).mappings().first()


@pay_blue.route('/order/<int:order_id>', methods=['GET'])
def order_detail(order_id):
    """查询订单可支付状态。前端轮询支付结果时也用它。"""
    with db.engine.begin() as conn:
        order = _fetch_order(conn, order_id)
    if order is None:
        return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
    return jsonify(ok=True, order=dict(order),
                   payable=(order['order_status'] == ORDER_UNPAID))


@pay_blue.route('/prepay/<int:order_id>', methods=['POST'])
@idempotent('prepay')
def prepay(order_id):
    """发起支付：返回给前端的网关调起参数。

    本阶段刻意只回 501：接口契约、锁、幂等、状态校验全部是真的，
    唯独"调网关"这一步没实现。这样前端可以先对接联调，也绝不会出现
    "假装支付成功"导致订单状态被错误推进的情况。
    """
    try:
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _fetch_order(conn, order_id, for_update=True)
                if order is None:
                    return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
                if order['order_status'] == ORDER_PAID:
                    return jsonify(ok=False, code='ALREADY_PAID', msg='订单已支付'), 409
                if order['order_status'] != ORDER_UNPAID:
                    return jsonify(ok=False, code='ORDER_CLOSED', msg='订单已关闭，无法支付'), 409

                # 金额一律以数据库为准，绝不接受前端传入的金额 ——
                # 否则改一下请求体就能 1 元买手机。
                params = {
                    'out_trade_no': order['transaction_id'],
                    'total_fee': str(order['amount']),
                }
            # 事务已结束（行锁已释放），下面才允许做网络 IO
            return jsonify(ok=False, code='PAY_NOT_IMPLEMENTED',
                           msg='支付网关尚未接入（P1-B 待办）',
                           out_trade_no=params['out_trade_no']), 501
    except LockBusyError as e:
        # 409 而不是 500：这是"另一个请求正在处理"，客户端稍后重试即可
        return jsonify(ok=False, code='LOCK_BUSY', msg=str(e)), 409
    except Exception:
        logger.exception('发起支付失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500


@pay_blue.route('/cancel/<int:order_id>', methods=['POST'])
@idempotent('cancel')
def cancel(order_id):
    """取消未支付订单。

    这是本阶段**唯一真的会写库**的支付类接口，所以它同时充当
    "条件 UPDATE + rowcount" 这个模式的样板：
    UPDATE 带上 WHERE order_status = 1，改到 0 行就说明状态已经变了。
    """
    try:
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _fetch_order(conn, order_id, for_update=True)
                if order is None:
                    return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
                if order['order_status'] == ORDER_PAID:
                    return jsonify(ok=False, code='ALREADY_PAID',
                                   msg='订单已支付，请走退款流程'), 409

                result = conn.execute(text('''
                    UPDATE orders
                       SET order_status = :cancelled
                     WHERE order_id = :oid
                       AND order_status = :unpaid
                '''), {'cancelled': ORDER_CANCELLED,
                      'unpaid': ORDER_UNPAID,
                      'oid': order_id})

            if result.rowcount == 0:
                # 状态在别处已经变过了（并发取消 / 回调刚把它标成已支付）
                return jsonify(ok=False, code='STATE_CHANGED', msg='订单状态已变化，请刷新'), 409
            return jsonify(ok=True, cancelled=True)
    except LockBusyError as e:
        return jsonify(ok=False, code='LOCK_BUSY', msg=str(e)), 409
    except Exception:
        logger.exception('取消订单失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500
```

---

## 9. 步骤 8：`views/__init__.py`（改 2 行）

把导入行和蓝图注册区改成：

```python
    from . import clear_input, ge_RFM, order, user, pay

    app.register_blueprint(clear_input.ci_blue)
    app.register_blueprint(ge_RFM.ge_RFM_blue)
    app.register_blueprint(order.order_blue)
    app.register_blueprint(user.user_blue)
    app.register_blueprint(pay.pay_blue)
```

---

## 10. 步骤 9：`tests/test_lock.py`（新建，完整文件）

> 2026-10-19 拆分：这个文件现在只放**锁 + 状态机**（7 条）。`@idempotent` 的 7 条回归测试单独放在
> `tests/test_idempotent.py`（验收结果见第 12 节；那个文件不做全文转载 —— 它的核心就是"造几种刁钻响应"，
> 对着第 7 节的实现读一遍就懂）。

```python
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
```

---

## 11. P1-B 待办（本阶段刻意没做）

1. **`payment_records` 流水表 + `order_no` 唯一键** —— 重复回调的最终防线（不依赖 Redis）。
2. **真实网关接入**：微信 V3 / 支付宝统一下单，替换 `prepay` 里的 501 占位。
3. **支付回调 `notify`**：验签 → 金额校验 → 条件 UPDATE → 写流水；
   **重复回调要返回成功**（否则平台会一直重试形成风暴），幂等交给唯一键而不是 Redis。
4. **`orders` 加列**：`pay_no`（唯一）、`paid_at`、`update_time`。
5. **退款**：同样走 `order_lock`，退款单号唯一键防重复退。
6. **看门狗续期**：业务耗时可能超过 TTL 时再加（支付下单通常不需要）。
7. **前端页面**：订单列表加"立即支付 / 取消"按钮，`X-Request-Id` 用
   `crypto.randomUUID()` 生成（同一次点击的重试必须复用同一个 id）。

---

## 12. 实测验收记录（真环境，不是纸上推演）

验证环境：本机 **Redis 3.0.504**（带密码，db1）、**MySQL 8.0.12**（临时库 `taobao_user_p1test`）、
解释器 `E:\conda_envs\flask_py`（redis-py 3.5.3）。
（**现在项目实际运行环境已统一为 `E:\conda_envs\data_analysis` + `redis==6.4.0`**，见下方 2026-10-19 补充验证。）

| 轮次 | 内容 | 结果 |
|---|---|---|
| 锁语义 14 项 | 抢锁 / 放锁 / TTL / 接管 / 续期 / 自旋等待 / 20 线程竞争 | 13/14（失败那条是**测试脚本自己写错**：在已持锁的情况下用 NX 去"劫持"自己，逻辑上不可能成功；已用正确测法补测 5/5） |
| SQL + 幂等 16 项 | 条件 UPDATE 的 rowcount / FOR UPDATE 行锁互斥 / 幂等只缓存 2xx / 10 线程并发 | **16/16** |
| 锁超时与释放 5 项 | 抢不到抛异常 / 异常路径不误删别人的锁 / 等待模式能等到 / 自动释放 / 无残留 | **5/5** |

关键实测数据：

- `SET key token NX PX 2000` → 返回值 `True`、`PTTL=2000`、2.2 秒后 key 自动消失（**不会死锁**）；
- 20 线程同时抢同一把锁 → **恰好 1 个赢家**；
- 10 线程并发取消同一订单 → 状态码 `[200, 409×9]`，`rowcount` 只命中 1 次；
- `SELECT ... FOR UPDATE` 真互斥：第二个事务**等了 5.02 秒**才拿到行锁；
- 幂等：2xx 落缓存并在重放时命中，501 / 409 **不落缓存**（失败可重试）；
- 轮询间隔写 50ms 时，实际拿到锁的延迟约 **300ms**（调度颗粒度决定，故 `wait_ms` 别设太小）；
- 全程真实库 `taobao_user.orders` 仍是 **1999 行**、无 `payment_records` 表（测试没污染生产库）。

### 2026-10-19 补充验证（`data_analysis` + redis-py 6.4.0 + 改造后的 `register_script` 写法）

`utils/lock.py` 从 `eval()` 改成 `register_script()`/EVALSHA 之后，用项目自己的模块在真 Redis 上重跑：

| 检查项 | 结果 |
|---|---|
| 抢锁互斥：第二个 `acquire()` 拿不到（`SET NX` 原子） | PASS |
| token 不匹配时不会误删别人的锁 | PASS |
| `renew()` 续期（PTTL 2795 → 3000）；错 token 返回 False | PASS |
| token 正确时能放锁 | PASS |
| TTL 到期锁自动消失，旧 token 再 release 返回 False | PASS |
| **`SCRIPT FLUSH` 后自动回退**（EVALSHA 报 NOSCRIPT → SCRIPT LOAD → 重试） | PASS |
| 两段脚本都已在 Redis 脚本缓存中 | PASS |
| 8 线程并发抢同一把锁 → 临界区最多 1 人 | PASS |

合计 **9/9**。这一轮专门验的是"生产用 EVALSHA 会不会因为脚本缓存失效而挂" —— 结论是不会，回退由 redis-py 自带。

### 2026-10-19 补充验证（幂等加固 + 两份回归测试落地）

`pytest -q` → **55 passed**（41 条原有 + `tests/test_idempotent.py` 7 条 + `tests/test_lock.py` 7 条）。

| 检查项 | 结果 |
|---|---|
| 幂等：JSON 的 2xx 写缓存，重放命中、业务只执行 1 次 | PASS |
| 幂等：**非 JSON 的 2xx 不写缓存**（坑 1 回归） | PASS |
| 幂等：**重放保留 201**（坑 2 回归） | PASS |
| 幂等：声明是 JSON 但 body 解析失败 → 也跳过缓存 | PASS |
| 幂等：缓存值被改坏 → 按未命中处理，不给用户 500 | PASS |
| 幂等：旧格式裸 payload 仍能重放（向后兼容） | PASS |
| 幂等：缺 `X-Request-Id` → 400 `MISSING_REQUEST_ID` 且不碰 Redis | PASS |
| 锁：6 条 Redis 语义（互斥 / TTL / 放锁 / 误删 / 接管 / `LockBusyError`） | PASS |
| 锁：条件 UPDATE 的 rowcount 状态机（临时库 `taobao_user_test`，自动建/删） | PASS |

**这一轮顺手修掉的两个「测试假绿」问题**（都是"显示跑过了、其实没跑"）：

1. **`utils/text_lock.py` 放错目录** —— 它其实是 `tests/test_lock.py` 的全文（docstring 自己
   写着跑法 `pytest tests/test_lock.py -v`），放在 `utils/` 下 pytest 根本不会收集
   （文件名也不匹配 `test_*.py`）。已 `git mv` 到 `tests/test_lock.py`。
2. **`_mysql_ok()` 的 skip 判断写反了** —— 原本连 `_uri(TEST_DB)`，而 `taobao_user_test`
   是测试**自己现建**的库，所以永远 `(1049, "Unknown database 'taobao_user_test'")` →
   状态机那条测试在每台机器上都被**静默 skip**。改成连服务器级 `_uri()` 之后它才第一次真正跑起来。

---

## 13. 本阶段你要执行的操作

```bash
# 1. 装依赖（跑项目用的是 data_analysis 环境；redis 必须锁 6.4.0，原因见第 3 节）
E:\conda_envs\data_analysis\python.exe -m pip install "redis==6.4.0"

# 2. 把 .env 里的 REDIS_PASSWORD 填上本机 Redis 密码
#    （本机 Redis 3.0.504 带密码；.env.example 里留空即可）

# 3. 起项目
E:\conda_envs\data_analysis\python.exe app.py
```

> ⚠️ **三个实测坑（下面是照抄版，别用本文档早先那套 curl 命令）**：
> 1. **本机 curl 走 schannel，必报 `SEC_E_NO_CREDENTIALS`（HTTP 000）** → 改用 PowerShell 的
>    `Invoke-RestMethod`（或 Python 脚本）。
> 2. **`/pay/*` 的 POST 受 P0-2 的全局 CSRF 钩子保护**：不带 token 直接 POST 得到的是
>    `400 CSRF 校验失败`，**不是**早先写的 400 MISSING_REQUEST_ID / 501。
>    正确姿势：先登录拿到 session，再把页面里的 `csrf_token` 放进 `X-CSRF-Token` 头
>    （`views/auth.py` 的 `_csrf_valid()` 接受这个头）。**登录成功后 token 会轮换**，
>    所以要在登录**之后**重新抓一次页面。
> 3. **示例订单号必须是真实值**：`taobao_user.orders` 的 `order_id` 范围是 **21991~23989**
>    （1999 行、目前全部 `order_status=1`），所以早先文档里的 `/pay/order/1` 会 404。

```powershell
# ① 登录：先抓登录页的 csrf_token，再提交（账号换成你自己 create_admin 建的）
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$page = Invoke-WebRequest 'http://127.0.0.1:5000/login' -WebSession $s -UseBasicParsing
$tok = ([regex]'name="csrf_token" value="([^"]+)"').Match($page.Content).Groups[1].Value
Invoke-WebRequest 'http://127.0.0.1:5000/login' -Method Post -WebSession $s -UseBasicParsing `
  -Body @{ mobile = '13700000002'; pwd = 'admin123'; csrf_token = $tok } | Out-Null

# ② 登录后 token 已轮换：重新抓一次页面拿新 token
$page = Invoke-WebRequest 'http://127.0.0.1:5000/customer/list' -WebSession $s -UseBasicParsing
$tok = ([regex]'name="csrf_token" value="([^"]+)"').Match($page.Content).Groups[1].Value

# 查订单状态 -> 200，payable=true
Invoke-RestMethod 'http://127.0.0.1:5000/pay/order/21991' -WebSession $s

# 缺 request_id -> 400 MISSING_REQUEST_ID（幂等入口守住了）
try { Invoke-RestMethod 'http://127.0.0.1:5000/pay/prepay/21991' -Method Post -WebSession $s `
        -Headers @{ 'X-CSRF-Token' = $tok } }
catch { "HTTP " + $_.Exception.Response.StatusCode.value__ }

# 发起支付 -> 501 PAY_NOT_IMPLEMENTED（网关未接，但锁和状态校验都真跑了）
try { Invoke-RestMethod 'http://127.0.0.1:5000/pay/prepay/21991' -Method Post -WebSession $s `
        -Headers @{ 'X-CSRF-Token' = $tok; 'X-Request-Id' = 'demo-prepay-1' } }
catch { "HTTP " + $_.Exception.Response.StatusCode.value__ }

# 取消订单 -> 200；同一个 request_id 再打一次 -> 200，但走的是幂等缓存
Invoke-RestMethod 'http://127.0.0.1:5000/pay/cancel/21991' -Method Post -WebSession $s `
  -Headers @{ 'X-CSRF-Token' = $tok; 'X-Request-Id' = 'demo-cancel-1' }
Invoke-RestMethod 'http://127.0.0.1:5000/pay/cancel/21991' -Method Post -WebSession $s `
  -Headers @{ 'X-CSRF-Token' = $tok; 'X-Request-Id' = 'demo-cancel-1' }

# 看 Redis 里的锁与幂等 key（<你的密码> 换成实际密码）
redis-cli -a <你的密码> -n 1 keys "*"
```

> ⚠️ **取消接口会真的写库**：上面那条会把 `order_id=21991` 从 `1`（已下单）改成 `3`（已取消），
> 而 `taobao_user` 是你的演示库。不想动它就先把 `schema.sql` 导进一个临时库（如
> `taobao_user_demo`）并把 `.env` 的 `DB_NAME` 指过去，跑完再切回来。

**收尾**：验证时建过一个临时库 `taobao_user_p1test`，**交付时已经删掉了**（你的 `taobao_user` 全程没被碰）。
如果你重跑验证脚本，需要自己先建它（`tests/test_lock.py` 会自动建/删 `taobao_user_test`，两回事）。

---

## 14. 端到端时序：每一步属于哪个大功能

这一节回答一个具体问题：**一次支付 / 取消请求经过哪些步骤，每一步到底是「幂等」在起作用，还是「Redis 锁」，还是「数据库状态机」？**
下面每一步都标了归属和代码位置。

### 14.1 四个大功能的职责边界（先看这张表）

| 大功能 | 解决什么问题 | 粒度（key / 范围） | 代码位置 | 失效时的兜底 |
|---|---|---|---|---|
| 认证 + CSRF（P0-2） | 谁有权限调、防跨站伪造请求 | 会话（session cookie） | `views/auth.py` 的 `before_app_request` 钩子 | 无，它是前置防线 |
| **幂等**（request_id + Redis 缓存） | 同一张「小票」重复投递 → 只执行一次，重试返回同一份响应 | **请求**：`idem:{prefix}:{request_id}` | `utils/idempotent.py` | 缓存丢了最多重复执行一次，由下面两层兜住 |
| **Redis 分布式锁** | **同一订单**的并发操作串行化（两个**不同** request_id 也互斥） | **订单**：`lock:pay:order:{order_id}` | `utils/lock.py` | TTL 自动过期（默认 10s）+ 数据库状态机 |
| **数据库状态机** | 状态已经被改过还在改 → 用条件 UPDATE 的 rowcount 判定 | **行**：`orders` 里的那一行 | `views/pay.py` | P1-B：`payment_records` + `order_no` 唯一键 |

一句话记：**锁管「并发」，幂等管「重投」，状态机管「最终正确」** —— 三层叠起来，任意一层失效都不会导致重复扣款。

### 14.2 一次 `POST /pay/cancel/21991` 的完整时序

（`cancel` 是本阶段**唯一真写库**的接口，所以拿它当样板；`prepay` 走到第 9 步返回 501 占位。）

| # | 步骤 | 属于哪个大功能 | 代码位置 | 失败时的表现 |
|---|---|---|---|---|
| 0 | 前端生成 request_id，请求带上 `X-Request-Id` 与 `X-CSRF-Token` | 幂等（客户端契约） | 前端 | 不带 → 下面 400 |
| 1 | 全局 CSRF 校验（只管 POST / PUT / PATCH / DELETE） | 安全层（P0-2） | `views/auth.py` `csrf_protect()` | 400 `CSRF 校验失败` |
| 2 | 取 request_id：请求头 > JSON 字段 > 表单字段；**取不到报错，绝不自动生成** | 幂等 | `idempotent.py:get_request_id()` | 400 `MISSING_REQUEST_ID` |
| 3 | 拼 key `idem:cancel:<rid>` → `GET` → **命中就返回上次那份 2xx 响应（不碰锁、不碰库）** | **幂等（读路径）** | `idempotent.py` 装饰器 | —— |
| 4 | `SET lock:pay:order:21991 <uuid4> NX PX 10000`；抢不到则在 `wait_ms` 内 50ms 自旋重试 | **Redis 锁** | `lock.py:order_lock()` / `acquire()` | 409 `LOCK_BUSY`（刻意不是 500） |
| 5 | 开事务 + `SELECT ... FOR UPDATE` 拿行锁 | 数据库并发控制 | `pay.py:_fetch_order(for_update=True)` | 锁等待超时 / 死锁 → 500 |
| 6 | 状态校验：已支付 → 409；已关闭 → 409 | 数据库状态机 | `pay.py:cancel()` | 409 `ALREADY_PAID` |
| 7 | `UPDATE orders SET order_status=3 WHERE order_id=… AND order_status=1`，**用 rowcount 判定** | **数据库状态机（最终防线）** | `pay.py:cancel()` | rowcount = 0 → 409 `STATE_CHANGED` |
| 8 | 退出事务 → 提交 + 释放行锁 | 数据库 | `with db.engine.begin()` 结束 | 异常自动回滚 |
| 9 | 需要调网关时：**在锁外、事务外**发网络请求（本阶段没有，直接 501） | 支付网关（P1-B 待办） | `pay.py:prepay()` | 501 `PAY_NOT_IMPLEMENTED` |
| 10 | 退出 `order_lock` → `finally` 放锁（EVALSHA + Lua 校验 token） | **Redis 锁** | `lock.py:redis_lock()` | token 不匹配只告警（锁已被接管） |
| 11 | 视图返回 2xx → `SET idem:cancel:<rid> <响应 JSON> PX 86400000` | **幂等（写路径）** | `idempotent.py` 装饰器 | 写失败只影响下次重试的短路 |
| 12 | 返回响应；同 request_id 重试 → 命中第 3 步 | 幂等 | —— | —— |

嵌套关系（从外到内）：

```text
CSRF 钩子（P0-2 安全层）
└─ idempotent 装饰器 ──────── 查缓存            ← 幂等·读
   └─ order_lock（Redis 锁）── SET NX PX        ← 锁·抢
      └─ db.engine.begin()（事务）
         ├─ SELECT ... FOR UPDATE（行锁）        ← 数据库·并发控制
         └─ 状态校验 + 条件 UPDATE + rowcount    ← 数据库·状态机（最终防线）
      ↑ 出事务 = 提交 + 释放行锁
      └─ 网络 IO（调网关）—— 必须在锁外
   ↑ 出 order_lock = finally 放锁               ← 锁·放
   └─ 写缓存                                    ← 幂等·写（在放锁之后！）
```

### 14.3 ⚠️ 已知竞态窗口（主动说出来，别等人问）

第 11 步在第 10 步**之后**，所以同一 request_id 的两个并发请求存在这个窗口：

```text
A: 查缓存(未命中) → 抢锁 → 业务 → 放锁 ──────────→ 写缓存
B:        查缓存(未命中) → 抢锁(A 已放) → 业务 ← 又执行了一次
```

- **为什么现在依然安全**：第 7 步的条件 UPDATE 会让第二次的 rowcount = 0 → 409；
  接上真网关后由 P1-B 的 `order_no` 唯一键 / `payment_records` 流水表兜底。
- **想彻底堵住**（二选一）：
  1. **占位式幂等**：查缓存前先 `SET idem:<rid> PENDING NX EX 30`，谁占到谁执行，另一个直接回「处理中」（要注意占位者崩溃后的自愈）；
  2. **request_id 维度的锁**：`lock:idem:<rid>` 再套一层（代价是多一次 Redis 往返）。
- **面试口径**：Redis 只负责**加速和串行化**，最终正确性落在数据库上。能主动说出这个窗口和兜底方案，比说「我做了幂等所以不会重复」可信得多。

---

## 附录：为什么数据库连接池不写成 `redis_client.py` 那种惰性单例？

`utils/db.py` 是模块级 `engine = create_engine(...)`，`utils/redis_client.py` 是 `get_redis()` 惰性单例。
两者不一样**不是因为风格不统一，而是因为这两个构造函数的行为根本不同**：

| | `create_engine(uri)` | `Redis(host=..., ...)` |
|---|---|---|
| 调用时会不会真的建立连接 | **不会**。只做参数解析、建连接池对象、加载 dialect，一次网络 IO 都没有 | **也不会**。2026-10-19 实测：`Redis(host='127.0.0.1', port=1, socket_connect_timeout=1)` 构造耗时 0.0002s、不抛异常，第一条命令（`ping()`）才 `TimeoutError`。~~"构造函数里就 connect() 打开 socket"~~ 这句是错的，此处更正 |
| 数据库 / Redis 没起时 | import 完全正常，第一次执行 SQL 才报错 | 同样 import 正常、第一次**命令**才报错（所以惰性化的理由不是"避免 import 崩溃"，见下面第 3 条） |
| 构造开销 | 极便宜（微秒级） | 同样极便宜（建池对象，微秒级）；TCP + 认证发生在第一条命令 |
| fork 安全（gunicorn `--preload`） | 只要没执行过 SQL，子进程不会共享 socket | 只要没执行过命令，也不会共享 socket；执行过才会 |

具体到这个项目的四条理由：

1. **`create_engine` 不连接，`Redis()` 同样不连接**（2026-10-19 实测更正：原文"构造即握手"是错的）。
   所以惰性化的价值不在"避免 import 阶段崩溃"，而在下面三条。
2. **符合 `config.py` 的 fail-fast 约定**：`DB_PASSWORD` 没配时 `Config.db_uri()` 直接抛
   `RuntimeError`，在 import 阶段就暴露配置错误 —— 这是刻意设计的，惰性化反而把错误推迟到某个请求里。
3. **import 图上的差异**：`utils.db` 被 `loader.py` 和所有 view 在模块顶层 import，
   **每条路径都必然加载它**，惰性化省不下任何东西；
   而 `redis_client` 只在"支付加锁"这一个场景才需要 —— RFM 分析、CSV 导入、商品管理全都不碰 Redis。
   **这时"惰性"才真正有价值**：开发/演示时不起 Redis，项目照样跑。
4. **参数特征不同**：engine 是应用级共享单例（`pool_size=30`）；Redis 连接池希望"更小、超时更短"，
   而 `socket_connect_timeout=1` 这种"支付链路快速失败"的诉求是支付专用的，
   不适合让全应用在 import 时就承担。

**什么时候该把 engine 也改成惰性？** 两个信号：

- 部署成 gunicorn `--preload` + 多 worker，担心连接池跨 fork 共享（那时要么 `post_fork` 里
  `engine.dispose()`，要么惰性化）；
- 想写"不连数据库也能跑的单元测试"。

改法（渐进式，不破坏现有调用点）：保留 `engine` 这个名字，只把构建推迟：

```python
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(Config.db_uri(), pool_recycle=3600, pool_pre_ping=True,
                                pool_size=30, max_overflow=20, pool_timeout=30)
    return _engine


class _EngineProxy:
    """`db.engine` 这种老写法继续可用，但直到真正用才建引擎。"""
    def __getattr__(self, item):
        return getattr(get_engine(), item)


engine = _EngineProxy()
```

因为 view 全是 `from utils import db` + `db.engine.xxx` 的写法（属性在运行时才解析），
这层代理**不用改任何调用点**就能生效。