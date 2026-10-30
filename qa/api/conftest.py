import secrets
import string
from multiprocessing import connection

import pytest
import requests
from sqlalchemy import text

from .client import ApiClient

from .config import settings
from utils import db

def search_phone(phone):
    with db.engine.begin() as conn:
        row = conn.execute(text(
            'select count(*) from users where phone = :phone'
        ),
        {'phone': phone}).scalar()
    return row

def gen_phone():
    # 1 + 第二位3-9 + 9位数字
    phone = "1" + secrets.choice("3456789") + "".join(
        secrets.choice(string.digits) for _ in range(9)
    )
    while search_phone(phone) :
        phone = "1" + secrets.choice("3456789") + "".join(
            secrets.choice(string.digits) for _ in range(9)
        )
    return phone

def gen_password(length=8):
    lowers = string.ascii_lowercase
    uppers = string.ascii_uppercase
    digits = string.digits
    specials = "!@#$%^&*"
    all_chars = lowers + uppers + digits + specials

    # 先保证四类字符各一个
    pwd = [
        secrets.choice(lowers),
        secrets.choice(uppers),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    # 补齐剩余位数
    pwd += [secrets.choice(all_chars) for _ in range(length - 4)]

    # 安全随机打乱
    rng = secrets.SystemRandom()
    rng.shuffle(pwd)
    return "".join(pwd)


@pytest.fixture(scope="session", autouse=True)
def server_ready():
    try:
        requests.get(settings.base_url + "/login", timeout=3)
    except requests.exceptions.RequestException:
        pytest.fail("连不上服务，"
                    "先去 E:\\Flask_app 跑 python app.py",
                    pytrace=False)
    return settings.base_url




@pytest.fixture
def client():
    return ApiClient()


@pytest.fixture
def staff_account():
    if not settings.has_staff:
        pytest.fail("去配 qa\\.env 的 STAFF_PHONE/STAFF_PASSWORD",
                    pytrace=False)
    else:
        return (settings.staff_phone,
                settings.staff_password,
                )


@pytest.fixture
def customer_account():
    if not settings.has_customer:
        pytest.fail("去配 qa\\.env 的 CUSTOMER_PHONE/CUSTOMER_PASSWORD",
                    pytrace=False)
    else:
        return (settings.customer_phone,
                settings.customer_password,
                )

@pytest.fixture
def register_account():
    phone = gen_phone()
    password = gen_password()
    return (phone, password)



@pytest.fixture
def staff_login(staff_account):
    api = ApiClient()
    resp = api.login(staff_account[0], staff_account[1])

    if resp.status_code != 302:
        pytest.fail(f"响应码错误,状态码:{resp.status_code},"
                    f"响应片段:{resp.text[:200]}", pytrace=False)
    else:
        return (api, resp)


@pytest.fixture
def customer_login(customer_account):
    api = ApiClient()
    resp = api.login(customer_account[0], customer_account[1])
    if resp.status_code != 302:
        pytest.fail(f"响应码错误,状态码:{resp.status_code},"
                    f"响应片段:{resp.text[:200]}", pytrace=False)
    else:
        return (api, resp)


@pytest.fixture
def staff_client(staff_login):
    api = staff_login[0]
    return api


@pytest.fixture
def customer_client(customer_login):
    api = customer_login[0]
    return api

