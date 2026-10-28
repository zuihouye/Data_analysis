import pytest
import requests

from .client import ApiClient

from .config import settings


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
