import sys

import pytest
from selenium import webdriver
from qa.api.config import settings
from qa.web.ui_client import UiClient
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
        #,logging.FileHandler('qa_web.log')
    ]
)

@pytest.fixture(scope="function")
def driver():
    options = webdriver.EdgeOptions()
    options.add_argument('--headless=new')
    driver = webdriver.Edge(options=options)

    yield driver
    driver.quit()


@pytest.fixture()
def ui_client(driver):
    client = UiClient(driver = driver)
    return client

# 截图钩子,测试失败截图保存
@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()

    if rep.when == 'call' and rep.failed:
        ui_client = item.funcargs.get("ui_client")
        if ui_client:
            ui_client.screenshot(rep)

@pytest.fixture()
def staff_account():
    if not settings.has_staff:
        pytest.fail('请先在qa/.env配置'
                    'STAFF_PHONE,STAFF_PASSWORD')
    else:
        return settings.staff_phone, settings.staff_password