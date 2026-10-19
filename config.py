import os
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv

# 显式指定路径，避免"cwd 不同就找不到 .env"的玄学问题
load_dotenv(Path(__file__).resolve().parent / '.env')

class Config:
    # ---------------------------------------------------------- 数据库
    DB_USER     = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_HOST     = os.getenv('DB_HOST', 'localhost')
    DB_PORT     = os.getenv('DB_PORT', '3306')
    DB_NAME     = os.getenv('DB_NAME', 'taobao_user')

    # ---------------------------------------------------------- 网页
    # 缺失时临时随机生成：方便本地起步，但重启即失效，所以只够开发用
    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)

    SESSION_COOKIE_HTTPONLY = True  # JS 读不到 cookie，XSS 偷不走 session
    SESSION_COOKIE_SAMESITE = 'Lax'  # 跨站请求不带 cookie，CSRF 的第一道防线
    SESSION_COOKIE_SECURE = False  # 本地是 http；上线换 https 后必须改成 True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # ---------------------------------------------------------- Redis
    # 只给支付锁 / 幂等用。用独立 db 索引，跟业务缓存分开：
    # 万一有人对 db0 执行 FLUSHDB，不会把正在生效的支付锁一起冲掉。
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB = int(os.getenv('REDIS_DB', '1'))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')

    # 锁参数集中放这里，测试可以覆盖\
    PAY_LOCK_TTL_MS = int(os.getenv('PAY_LOCK_TTL_MS', '10000'))
    PAY_LOCK_WAIT_MS = int(os.getenv('PAY_LOCK_WAIT_MS', '3000'))
    PAY_IDEMPOTENT_TTL = int(os.getenv('PAY_IDEMPOTENT_TTL', '86400'))

    @classmethod
    def redis_kwargs(cls):
        return dict(host=cls.REDIS_HOST, port=cls.REDIS_PORT,
                    db=cls.REDIS_DB, password=cls.REDIS_PASSWORD or None,
                    decode_responses=True,  # 必须 True，否则 Lua 返回 bytes，token 比较会全不相等
                    socket_connect_timeout=1,  # 支付链路里 Redis 挂了要"快速失败"，不能挂住请求
                    socket_timeout=1,
                    health_check_interval=30
                    )

    # 仅在测试里允许关掉；生产必须是 True
    CSRF_ENABLED = True

    @classmethod
    def db_uri(cls):
        if not cls.DB_PASSWORD:
            raise RuntimeError(
                "环境变量 DB_PASSWORD 未设置。"
                "请复制 .env.example 为 .env 并填写密码。"
            )
        return (f"mysql+pymysql://{cls.DB_USER}:{quote_plus(cls.DB_PASSWORD)}"
                f"@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}?charset=utf8mb4")