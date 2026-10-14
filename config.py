import os
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv

# 显式指定路径，避免"cwd 不同就找不到 .env"的玄学问题
load_dotenv(Path(__file__).resolve().parent / '.env')

class Config:
    DB_USER     = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_HOST     = os.getenv('DB_HOST', 'localhost')
    DB_PORT     = os.getenv('DB_PORT', '3306')
    DB_NAME     = os.getenv('DB_NAME', 'taobao_user')

    @classmethod
    def db_uri(cls):
        if not cls.DB_PASSWORD:
            raise RuntimeError(
                "环境变量 DB_PASSWORD 未设置。"
                "请复制 .env.example 为 .env 并填写密码。"
            )
        return (f"mysql+pymysql://{cls.DB_USER}:{quote_plus(cls.DB_PASSWORD)}"
                f"@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}?charset=utf8mb4")