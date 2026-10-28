# 只写：怎么找、怎么点、怎么等、怎么取证（与具体页面无关）
# 封装：打开、找元素、点、取文本、等待、截图、退出登录
# open/ find /click /text /wait_visible /screenshot /quit
# —— 用显式等待，禁用 time.sleep；
import os
import time

from selenium.common import TimeoutException
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from qa.api.config import settings

import logging
web_logger = logging.getLogger(__name__)



class UiClient:
    def __init__(self,driver,timeout = None,base_url = None):
        self.base_url = (base_url or settings.base_url).rstrip('/')
        self.driver = driver
        self.timeout =(timeout or settings.timeout)


    # 拼接url并打开
    def open(self,path):
        if path.startswith('http://') or path.startswith('https://'):
            url = path
        else:
            url = self.base_url + "/" + path.lstrip('/')


        self.driver.get(url)

        return self # 链式调用

    # 等待
    def wait_until(self, condition):
        return WebDriverWait(self.driver, self.timeout).until(condition)

    # 找到对应位置元素(要求可见),(默认CSS)
    def find(self,locator):
        by,location = locator
        try:
            elem = self.wait_until(
                EC.visibility_of_element_located((by,location))
            )
        except TimeoutException:
            web_logger.error(f"超时或无法找到当前元素:{locator}\n"
                             f"url:{self.driver.current_url}")
            self.screenshot()
            raise

        return elem

    # 输入文本
    def send_text(self,locator,text = ''):
        elem = self.find(locator)
        elem.send_keys(text)
        return self

    # 点击
    def click(self,locator):
        self.find(locator).click()
        return self # -p

    # 取文本
    def text(self,locator):
        return self.find(locator).text

    # 等待path出现在url
    def url_contains(self,path):
        self.wait_until(EC.url_contains(path))
        return self

    # 截图
    def screenshot(self,name = "debug",folder ="qa/web/screenshots"):
        # 创建文件夹
        os.makedirs(folder,exist_ok=True)
        # 时间戳
        timestamp = time.strftime("%Y%m%d_%H%M%S") #
        file_path = os.path.join(folder,f"{name}_{timestamp}.png")
        # 保存截图
        success = self.driver.save_screenshot(file_path)
        if not success:
            web_logger.error("截图失败")
            return None
        else:
            web_logger.info("截图已保存:%s" % file_path)
            return file_path







