
class TestRegister:

    def test_register_success(self, register_account, client):
        phone = register_account[0]
        password = register_account[1]
        csrf_token = client.get_csrf_token(path='/login')
        resp = client.post('/register', data={'phone': phone,
                                       'username': 'Test',
                                       'region': 'Guizhou',
                                       'pwd': password,
                                       'pwd2': password,
                                       'csrf_token': csrf_token},
                                        allow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get('Location') == '/product/list'


