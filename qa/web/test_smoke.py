from . import customer_list_page
from .login_page import LoginPage
from .customer_list_page import CustomerListPage
from selenium.webdriver.support import expected_conditions as EC


class TestLoginPage:
    # 员工登录成功，
    def test_login(self, staff_account, ui_client):
        page = LoginPage(ui_client)
        customer_page = CustomerListPage(ui_client)
        # 打开并进入登录页面
        page.open('/login')
        # 登录
        page.click_login(staff_account[0], staff_account[1])
        # 等待跳转/customer/list
        ui_client.wait_until(
            EC.url_contains('/customer/list')
        )
        assert '/customer/list' in page.get_url()
        # 页面标题'客户管理'
        assert '客户管理' == customer_page.title_text() # 精确值用==


    # 密码错误
    def test_login_wrong_password(self, ui_client, staff_account):
        page = LoginPage(ui_client)
        # 打开并进入登录页面
        page.open('/login')
        # 登录
        page.click_login(staff_account[0], 'wrong_password')
        # 等待条件要选成功之后才会出现的东西
        assert page.find(LoginPage.flash_loc)
        assert '/login' in page.get_url() # 多余，出不出都包含
        assert '手机号或密码错误' in page.error_text()


    # 未登录访问被拦截
    def test_anonymous_redirect(self, ui_client):
        page = LoginPage(ui_client)
        # 直接访问订单页面
        page.open('/order/list')
        # 等待返回登录页面
        assert 'login' in page.get_url()
        # 出现手机号输入框
        assert page.find(LoginPage.mobile_loc) # find居然可以直接断言

