from urllib.parse import urlparse, parse_qs


class TestLogin:

    def test_login_success(self, staff_login):
        api, resp = staff_login
        # 登录成功返回302
        assert resp.status_code == 302
        # Location = /customer/list
        location = resp.headers.get("Location",'')
        assert location.endswith('/customer/list')
        # 同一个 api 再 GET /customer/list,证明 cookie 登录态真的生效
        resp_next = api.get('/customer/list')
        assert resp_next.status_code == 200
        assert '客户列表' in resp_next.text

    def test_login_wrong_password(self, client, staff_account):
        csrf_token = client.get_csrf_token(path='/login')
        resp = client.post(path='/login', data={
            "mobile": staff_account[0],
            "pwd": "test_wrong_pwd",  # 错误密码
            "csrf_token": csrf_token,
        }, allow_redirects=False)
        assert resp.status_code == 401
        client.assert_text(resp, "手机号或密码错误")  # 用户不存在时也是这一句，防止枚举
