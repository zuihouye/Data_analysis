# 只写：这个页面有哪些元素、能做什么动作
from selenium.webdriver.common.by import By



class LoginPage:

    # 手机号输入框
    mobile_loc = (By.CSS_SELECTOR,'#mobile')
    # 密码输入框
    password_loc = (By.CSS_SELECTOR,'#pwd')
    # 登录按钮
    login_btn_loc = (By.XPATH,
                     '//button[text()="登录"]')
    # flash
    flash_loc = (
        By.XPATH,
        '//div[contains(.,"手机号或密码错误")]'
    )

    def __init__(self, ui_client):
        self.ui_client = ui_client

    def find(self,locator):
        return self.ui_client.find(locator)

    # 打开页面
    def open(self,path):
        self.ui_client.open(path)
    # 输入手机号
    def text_phone(self, phone):
        elem = self.ui_client.find(self.mobile_loc)
        elem.send_keys(phone)
    # 输入密码
    def text_password(self, password):
        elem = self.ui_client.find(self.password_loc)
        elem.send_keys(password)
    # 登录
    def click_login(self,phone,password):
        self.text_phone(phone)
        self.text_password(password)
        elem = self.ui_client.find(self.login_btn_loc)
        elem.click()

    # 返回url
    def get_url(self):
        url = self.ui_client.driver.current_url
        return url

    # 返回flash文本
    def error_text(self):
        elem = self.ui_client.find(self.flash_loc)
        return elem.text



