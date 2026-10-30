from selenium.webdriver.common.by import By

from qa.web.base.base_page import BasePage


class CustomerListPage(BasePage):

    # 标题
    title_loc = (By.XPATH, '//h1[text()="客户管理"]')

    def __init__(self, driver):
        super().__init__(driver)


    # 返回标题文本
    def title_text(self):
        elem = self.find(self.title_loc)
        return elem.text

