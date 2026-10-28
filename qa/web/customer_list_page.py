from selenium.webdriver.common.by import By


class CustomerListPage:

    # 标题
    title_loc = (By.XPATH, '//h1[text()="客户管理"]')

    def __init__(self, ui_client):
        self.ui_client = ui_client


    # 返回标题文本
    def title_text(self):
        elem = self.ui_client.find(self.title_loc)
        return elem.text

