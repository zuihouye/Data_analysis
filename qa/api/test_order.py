from urllib.parse import urlparse, parse_qs


class TestOrder:

    def test_order_list_staff(self, staff_client):
        resp = staff_client.get('/order/list')
        staff_client.assert_status_code(resp, 200)
        staff_client.assert_text(resp, '订单管理')

    # 模拟未登录访问订单页
    def test_order_list_anonymous_redirects(self, client):
        resp = client.get('/order/list', allow_redirects=False)
        client.assert_status_code(resp, 302)
        location = resp.headers.get('Location', '')  # p
        # 解析url(生产环境中可能会编码)
        # Location: /login?next=/order/list ->/login?next=%2Forder%2Flist
        parsed_url = urlparse(location)
        # 1 .断言/login
        assert '/login' == parsed_url.path
        # 2.获取参数列表,断言
        params = parse_qs(parsed_url.query)
        assert params.get('next', '') == ['/order/list']

    # 模拟员工下单，期望失败
    def test_order_create_forbidden_for_staff(self, staff_client):
        resp = staff_client.get('/order/create')
        staff_client.assert_status_code(resp, 403)
        staff_client.assert_text(resp, '没有访问权限')  # p

    # 模拟顾客无csrf_token,创建订单
    def test_order_create_missing_csrf(self, customer_client, customer_account):
        resp = customer_client.post('/order/create',
                                    data=dict(product_id='PROD-005',
                                              quantity='1')
                                    )
        customer_client.assert_status_code(resp, 400)
        customer_client.assert_text(resp, "CSRF 校验失败：请刷新页面后重试。")
