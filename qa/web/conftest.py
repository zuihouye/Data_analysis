import sys

import pytest
from selenium import webdriver
from qa.api.config import settings

import logging

from qa.web.base.base_page import BasePage
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
        ,logging.FileHandler('qa_web.log')
    ]
)
# 创建浏览器连接，执行测试后回收
@pytest.fixture(scope="function")
def driver():
    options = webdriver.EdgeOptions()
    options.add_argument('--headless=new')
    driver = webdriver.Edge(options=options)

    yield driver
    driver.quit()




# 截图钩子,测试失败截图保存
@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item):
    outcome = yield
    rep = outcome.get_result()

    if rep.when == 'call' and rep.failed:
        driver = item.funcargs.get("driver")
        if driver:
            BasePage(driver).screenshot(name = item.name)

@pytest.fixture()
def staff_account():
    if not settings.has_staff:
        pytest.fail('请先在qa/.env配置'
                    'STAFF_PHONE,STAFF_PASSWORD')
    else:
        return settings.staff_phone, settings.staff_password