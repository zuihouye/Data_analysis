import os
from pathlib import Path

from dotenv import load_dotenv

# 测试手机号,密码,默认系统地址等路径放到.env
text_env_path = Path(__file__).resolve().parents[1] / ".env"

load_dotenv(dotenv_path=text_env_path)

ENVIRONMENTS = {"local": "http://127.0.0.1:5000"}


class Settings:
    def __init__(self):
        # 实例属性:
        # app_env,base_url
        # staff_phone / staff_password / customer_phone / customer_password
        # timeout

        self.app_env = os.getenv("APP_ENV", "local")  # 读取APP_ENV

        # address:优先级BASE_URL环境变量 > ENVIRONMENTS[app_env] > ENVIRONMENTS["local"]，末尾去掉 /
        self.base_url = (os.getenv("BASE_URL")
                         or ENVIRONMENTS.get(self.app_env)
                         or ENVIRONMENTS["local"])

        self.base_url = self.base_url.rstrip("/")

        # 账号密码
        self.staff_phone = os.getenv("STAFF_PHONE")
        self.staff_password = os.getenv("STAFF_PASSWORD")
        self.customer_phone = os.getenv("CUSTOMER_PHONE")
        self.customer_password = os.getenv("CUSTOMER_PASSWORD")

        # 接口请求超时时间
        self.timeout = float(os.getenv("TIMEOUT", "10"))

    # 是否有员工或者顾客
    @property
    def has_staff(self):
        return bool(self.staff_phone and self.staff_password)

    @property
    def has_customer(self):
        return bool(self.customer_phone and self.customer_password)


settings = Settings()
