import logging

from bs4 import BeautifulSoup

import requests

from qa.api.config import settings

api_logging = logging.getLogger(__name__)


class ApiClient:

    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or settings.base_url).rstrip('/')
        self.timeout = timeout or settings.timeout
        self.session = requests.Session()

    # 拼接url
    def url(self, path):
        if path.startswith('http://') or path.startswith('https://'):
            return path
        else:
            return self.base_url + "/" + path.lstrip('/')

    # 调用_send发送get请求获取表单
    def get(self, path, **kwargs):
        resp = self._send("GET", path, **kwargs)
        return resp

    # 调用_send发送post请求获取表单
    def post(self, path, data=None, **kwargs):
        resp = self._send("POST", path, data, **kwargs)
        return resp

    def _send(self, method, path, data=None, **kwargs):
        api_logging.info("method:%s url:%s", method, self.url(path))
        resp = self.session.request(method, self.url(path), timeout=self.timeout, data=data, **kwargs)
        api_logging.info("状态码:%s,url:%s,响应类型:%s,大小:%s", resp.status_code, resp.url,
                         resp.headers.get("content-type"), len(resp.content))
        return resp

    # 获取csrf_token
    def get_csrf_token(self, path='/login'):
        resp = self.get(path)
        soup = BeautifulSoup(resp.text, 'html.parser')
        token_input = soup.find("input", attrs={"name": "csrf_token"})

        if token_input and token_input.get("value"):
            return token_input.get("value")
        else:
            raise AssertionError("未获取到CSRF_TOKEN,无法登录")

    # 登录
    def login(self, phone, password):
        phone = phone or settings.staff_phone or settings.customer_phone
        password = password or settings.staff_password or settings.customer_password
        csrf_token = self.get_csrf_token('/login')
        resp = self.post('/login',
                         data={
                             'mobile': phone,
                             'pwd': password,
                             'csrf_token': csrf_token,
                         },
                         allow_redirects=False)
        return resp

    # 断言状态码
    def assert_status_code(self, resp, expected_status_code):
        assert resp.status_code == expected_status_code, \
            f"结果状态码与预期状态码不一致,状态码:{resp.status_code},url:{resp.url},响应片段:{resp.text[:200]}"

    # 断言页面出现某段文案
    def assert_text(self, resp, expected_text):

        assert bool(
            expected_text in resp.text
        ), f"页面不存在预期文案,状态码:{resp.status_code},url:{resp.url},响应片段:{resp.text[:200]}"
