class TestProduct:

    # 测试员工是否能正常进入产品页
    def test_product_list_staff(self, staff_client):
        resp = staff_client.get('/product/list')
        staff_client.assert_status_code(resp, 200)
        staff_client.assert_text(resp, '产品管理')

    # 越权:测试顾客能否进添加产品接口
    def test_product_add_forbidden_for_customer(self, customer_client):
        resp = customer_client.get('/product/add')
        customer_client.assert_status_code(resp, 403)
        customer_client.assert_text(resp, '需要员工或管理员权限')

    # 越权:测试顾客能否看到下架产品
    def test_product_list_customer_ignore_status(self, customer_client):
        resp = customer_client.get('/product/list',
                                   params={'status': '2'})  # 'status':'2'表示下架
        customer_client.assert_status_code(resp, 200)
        assert '已下架' not in resp.text

    # 测试传非法参数是否能回到默认页面
    # page=0&size=999&status=9&sort=xxx
    def test_product_list_bad_params_fallback(self, staff_client):
        resp = staff_client.get('/product/list'
                                , params=dict(page='0',
                                              size='999',
                                              status='9',
                                              sort='xxx')
                                )
        staff_client.assert_status_code(resp, 200)
        staff_client.assert_text(resp, 'name="size" value="20"')
