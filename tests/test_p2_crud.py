"""P2 集成测试：权限矩阵 / 客户下单 / 改单与取消 / 确认收款 / 软删除 / 导入守卫。

与 tests/test_import_db.py 一样的玩法：
  - 临时库 taobao_user_test，模块级 fixture 建库 → 跑 schema.sql → 导入真实 CSV → 跑完 DROP；
  - 所有 ops 测试都 monkeypatch utils.db.engine 指向临时库；
  - MySQL 不可用时整组 skip。

覆盖的核心安全性质：
  1. 未登录写操作 → 302 登录页；客户访问员工页 → 403；
  2. 客户只能看/改自己的订单（IDOR 修复）；
  3. 金额永远由服务端按数据库里的单价算，前端传什么都不影响；
  4. 已支付订单不可改、不可取消；确认收款重复执行返回 ALREADY_PAID；
  5. 删除全部是软删除，行还在、外键不炸；
  6. 系统里出现业务数据后，全量导入被拒绝（BusinessDataGuardError）。
"""
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

from config import Config
from utils import db as dbmod
from utils.loader import BusinessDataGuardError, write_to_db
from utils.order_ops import (OrderError, cancel_order, confirm_payment,
                             create_order, update_quantity)
from utils.quality import clean, validate

CSRF_TOKEN = 'test-csrf-token'
PASSWORD = 'secret123'
ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / 'schema.sql'
CSV = ROOT / 'static' / 'archive' / 'raw_rfm_sales_transactions_(V2).csv'
TEST_DB = 'taobao_user_test'


# ---------------------------------------------------------------- 建库工具

def _uri(database=''):
    auth = f'{Config.DB_USER}:{quote_plus(Config.DB_PASSWORD)}'
    return (f'mysql+pymysql://{auth}@{Config.DB_HOST}:{Config.DB_PORT}'
            f'/{database}?charset=utf8mb4')


def _statements():
    """读 schema.sql，去掉注释行，按分号拆成可执行语句"""
    lines = [ln for ln in SCHEMA_SQL.read_text(encoding='utf-8').splitlines()
             if not ln.strip().startswith('--')]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def _count(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _one(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).mappings().first()


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope='module')
def test_engine():
    server = create_engine(_uri())
    try:
        with server.begin() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {TEST_DB}'))
            conn.execute(text(f'CREATE DATABASE {TEST_DB} '
                              f'DEFAULT CHARACTER SET utf8mb4'))
    except Exception as exc:
        server.dispose()
        pytest.skip(f'MySQL 不可用，跳过 P2 集成测试: {type(exc).__name__}')

    engine = create_engine(_uri(TEST_DB))
    with engine.begin() as conn:
        for stmt in _statements():
            conn.execute(text(stmt))

    yield engine

    engine.dispose()
    with server.begin() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS {TEST_DB}'))
    server.dispose()


@pytest.fixture(scope='module')
def prepared():
    """真实 CSV 经 clean + validate，过滤脏行后的数据（= 导入会写库的那份）"""
    report, mask = validate(clean(pd.read_csv(CSV)))
    assert not report['fatal']
    return clean(pd.read_csv(CSV))[~mask].reset_index(drop=True)


@pytest.fixture(scope='module')
def loaded(test_engine, prepared):
    """把 CSV 数据灌进临时库（此时库是空的，导入守卫会放行）"""
    with test_engine.begin() as conn:
        write_to_db(prepared, conn)
    return prepared


@pytest.fixture(scope='module')
def accounts(test_engine, loaded):
    """基线账号 + 测试商品。

    直接写库而不是走注册路由：注册路由本身由 test_register_* 单独测，
    这里只要一个确定的数据基线。两个客户用来验证数据隔离。
    """
    users = [
        ('13800000001', '客户一', 0, 'REG-testcustomer1'),
        ('13800000002', '客户二', 0, 'REG-testcustomer2'),
        ('13800000003', '员工小王', 1, None),
        ('13800000004', '管理员老张', 2, None),
    ]
    with test_engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO customers (customer_id, region, cus_status) VALUES '
            "('REG-testcustomer1', 'Mandalay', 1), "
            "('REG-testcustomer2', 'Yangon', 1)"))
        conn.execute(text(
            'INSERT INTO products '
            '(product_id, product_name, product_category, ppu, product_status) VALUES '
            "('SKU-test-1', '测试商品A', 'Test', 12.50, 1), "
            "('SKU-test-2', '测试商品B', 'Test', 30.00, 1)"))
        for phone, username, role, cid in users:
            conn.execute(text(
                'INSERT INTO users (phone, username, password_hash, role, customer_id) '
                'VALUES (:p, :n, :h, :r, :cid)'),
                {'p': phone, 'n': username, 'h': generate_password_hash(PASSWORD),
                 'r': role, 'cid': cid})
    return {
        'customer1': 'REG-testcustomer1',
        'customer2': 'REG-testcustomer2',
        'customer1_phone': '13800000001',
        'customer2_phone': '13800000002',
        'staff_phone': '13800000003',
        'admin_phone': '13800000004',
    }


@pytest.fixture
def db(test_engine, monkeypatch):
    """把 utils.db.engine 指向临时库。

    注意：所有业务模块都写 `from utils import db` 再在调用时访问 `db.engine`，
    所以 monkeypatch 模块属性即可全局生效。如果哪个文件写成了
    `from utils.db import engine`，这里就 pat 不到 —— 那属于代码需要改的 bug。
    """
    monkeypatch.setattr(dbmod, 'engine', test_engine)
    return test_engine


@pytest.fixture
def app(db):
    from views import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------- 测试小工具

def _login(client, phone, expect_location=None):
    """登录并返回轮换后的 CSRF token（登录成功会换 token，旧 token 立刻失效）。"""
    with client.session_transaction() as sess:
        sess['_csrf_token'] = CSRF_TOKEN
    resp = client.post('/login', data={'mobile': phone, 'pwd': PASSWORD,
                                       'csrf_token': CSRF_TOKEN})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    if expect_location:
        assert expect_location in resp.headers['Location']
    with client.session_transaction() as sess:
        return sess['_csrf_token']


def _new_order(engine, customer_id, product_id='SKU-test-1', quantity=2):
    order = create_order(customer_id, product_id, quantity)
    return order


def _staff_id(engine, phone='13800000003'):
    return _one(engine, 'SELECT id FROM users WHERE phone = :p', p=phone)['id']


# ============================================================ 一、order_ops

def test_create_order_uses_server_side_price(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 3)
    assert order['amount'] == Decimal('37.50')      # 12.50 × 3

    row = _one(db, 'SELECT * FROM orders WHERE order_id = :oid',
               oid=order['order_id'])
    assert row['customer_id'] == 'REG-testcustomer1'
    assert row['product_id'] == 'SKU-test-1'
    assert row['quantity'] == 3
    assert row['order_status'] == 1
    assert str(row['transaction_id']).startswith('W')   # 网页订单前缀，和 CSV 的 T 区分


def test_create_order_rejects_off_shelf_product(db, accounts):
    with db.begin() as conn:
        conn.execute(text("UPDATE products SET product_status = 2 "
                          "WHERE product_id = 'SKU-test-2'"))
    try:
        with pytest.raises(OrderError) as excinfo:
            create_order('REG-testcustomer1', 'SKU-test-2', 1)
        assert excinfo.value.code == 'PRODUCT_OFF_SHELF'
    finally:
        with db.begin() as conn:
            conn.execute(text("UPDATE products SET product_status = 1 "
                              "WHERE product_id = 'SKU-test-2'"))


def test_create_order_rejects_bad_quantity(db):
    for bad in (0, -1, 1000, 'abc'):
        with pytest.raises(OrderError) as excinfo:
            create_order('REG-testcustomer1', 'SKU-test-1', bad)
        assert excinfo.value.code == 'BAD_QUANTITY'


def test_update_quantity_uses_snapshot_price(db, accounts):
    """商品调价后，改数量也必须按下单时的 ppu 快照算，而不是新价格。"""
    order = create_order('REG-testcustomer1', 'SKU-test-1', 1)   # ppu=12.50 快照
    with db.begin() as conn:
        conn.execute(text("UPDATE products SET ppu = 99.99 "
                          "WHERE product_id = 'SKU-test-1'"))
    try:
        update_quantity(order['order_id'], 4,
                        customer_id='REG-testcustomer1', as_staff=False)
        row = _one(db, 'SELECT quantity, ppu, amount FROM orders WHERE order_id = :oid',
                   oid=order['order_id'])
        assert row['quantity'] == 4
        assert row['ppu'] == Decimal('12.50')
        assert row['amount'] == Decimal('50.00')
    finally:
        with db.begin() as conn:
            conn.execute(text("UPDATE products SET ppu = 12.50 "
                              "WHERE product_id = 'SKU-test-1'"))


def test_update_quantity_of_other_customer_is_not_found(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 1)
    with pytest.raises(OrderError) as excinfo:
        update_quantity(order['order_id'], 5,
                        customer_id='REG-testcustomer2', as_staff=False)
    assert excinfo.value.code == 'ORDER_NOT_FOUND'


def test_cancel_order_is_soft_delete(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 1)
    before = _count(db, 'SELECT COUNT(*) FROM orders WHERE order_id = :oid',
                    oid=order['order_id'])
    cancel_order(order['order_id'],
                 customer_id='REG-testcustomer1', as_staff=False)
    row = _one(db, 'SELECT order_status FROM orders WHERE order_id = :oid',
               oid=order['order_id'])
    assert row['order_status'] == 3
    after = _count(db, 'SELECT COUNT(*) FROM orders WHERE order_id = :oid',
                   oid=order['order_id'])
    assert before == after == 1, '软删除：行必须还在'


def test_cancel_twice_second_time_order_closed(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 1)
    cancel_order(order['order_id'], as_staff=True)
    with pytest.raises(OrderError) as excinfo:
        cancel_order(order['order_id'], as_staff=True)
    assert excinfo.value.code == 'ORDER_CLOSED'


def test_confirm_payment_sets_audit_fields(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 2)
    staff_id = _staff_id(db)
    confirm_payment(order['order_id'], staff_user_id=staff_id)

    row = _one(db, 'SELECT order_status, paid_at, paid_by FROM orders '
                   'WHERE order_id = :oid', oid=order['order_id'])
    assert row['order_status'] == 2
    assert row['paid_at'] is not None          # 谁、什么时候确认的，可审计
    assert row['paid_by'] == staff_id

    with pytest.raises(OrderError) as excinfo:
        confirm_payment(order['order_id'], staff_user_id=staff_id)
    assert excinfo.value.code == 'ALREADY_PAID'


def test_paid_order_cannot_be_cancelled_or_updated(db, accounts):
    order = create_order('REG-testcustomer1', 'SKU-test-1', 1)
    confirm_payment(order['order_id'], staff_user_id=_staff_id(db))

    with pytest.raises(OrderError) as excinfo:
        cancel_order(order['order_id'], as_staff=True)
    assert excinfo.value.code == 'ALREADY_PAID'

    with pytest.raises(OrderError) as excinfo:
        update_quantity(order['order_id'], 9, as_staff=True)
    assert excinfo.value.code == 'ALREADY_PAID'


# ============================================================ 二、认证与注册

def test_register_creates_customers_and_users_rows(client, test_engine):
    phone = '13900000001'
    with client.session_transaction() as sess:
        sess['_csrf_token'] = CSRF_TOKEN
    resp = client.post('/register', data={
        'phone': phone, 'username': '新客户', 'region': 'Mandalay',
        'pwd': PASSWORD, 'pwd2': PASSWORD, 'csrf_token': CSRF_TOKEN,
    })
    assert resp.status_code == 302
    assert '/product/list' in resp.headers['Location']   # 客户注册后进商品页

    user = _one(test_engine, 'SELECT * FROM users WHERE phone = :p', p=phone)
    assert user['role'] == 0
    assert user['customer_id'].startswith('REG-')
    customer = _one(test_engine, 'SELECT * FROM customers WHERE customer_id = :cid',
                    cid=user['customer_id'])
    assert customer is not None and customer['cus_status'] == 1

    with client.session_transaction() as sess:
        assert sess['role'] == 0
        assert sess['customer_id'] == user['customer_id']   # 注册即登录


def test_register_duplicate_phone_is_409(client):
    with client.session_transaction() as sess:
        sess['_csrf_token'] = CSRF_TOKEN
    resp = client.post('/register', data={
        'phone': '13800000001',            # accounts fixture 里已存在的手机号
        'username': '重复注册', 'region': 'Yangon',
        'pwd': PASSWORD, 'pwd2': PASSWORD, 'csrf_token': CSRF_TOKEN,
    })
    assert resp.status_code == 409
    assert '已注册' in resp.get_data(as_text=True)


def test_login_customer_redirects_to_product_list(client, accounts):
    _login(client, accounts['customer1_phone'], expect_location='/product/list')


def test_anonymous_write_goes_to_login(client):
    with client.session_transaction() as sess:
        sess['_csrf_token'] = CSRF_TOKEN
    resp = client.get('/customer/list')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']

    resp = client.post('/order/delete', data={'order_id': 1,
                                              'csrf_token': CSRF_TOKEN})
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_create_routes_are_login_only(client):
    """POST-only 路由用 GET 访问返回 405（不可能"点个链接触发写操作"）。"""
    assert client.get('/product/delete').status_code == 405
    assert client.get('/order/delete').status_code == 405
    assert client.get('/customer/delete').status_code == 405


# ============================================================ 三、权限矩阵

def test_customer_is_forbidden_on_staff_pages(client, accounts):
    _login(client, accounts['customer1_phone'])
    assert client.get('/customer/list').status_code == 403
    assert client.get('/ge_rfm').status_code == 403
    assert client.get('/product/add').status_code == 403
    assert client.get('/order/create').status_code == 200   # 下单页客户可用


def test_staff_can_add_update_delete_product(client, test_engine, accounts):
    token = _login(client, accounts['staff_phone'])

    # 新增
    resp = client.post('/product/add', data={
        'product_name': '员工新增商品', 'product_category': 'Test',
        'ppu': '8.80', 'csrf_token': token,
    })
    assert resp.status_code == 302
    product = _one(test_engine,
                   'SELECT * FROM products WHERE product_name = :n',
                   n='员工新增商品')
    assert product['product_id'].startswith('SKU-')
    assert product['product_status'] == 1

    # 编辑
    resp = client.post(f"/product/update?product_id={product['product_id']}", data={
        'product_name': '员工新增商品（改）', 'product_category': 'Test',
        'ppu': '9.90', 'product_status': '1', 'csrf_token': token,
    })
    assert resp.status_code == 302
    assert _one(test_engine, 'SELECT ppu FROM products WHERE product_id = :p',
                p=product['product_id'])['ppu'] == Decimal('9.90')

    # 下架（软删除）
    resp = client.post('/product/delete', data={
        'product_id': product['product_id'], 'csrf_token': token,
    })
    assert resp.status_code == 302
    row = _one(test_engine, 'SELECT product_status FROM products '
                            'WHERE product_id = :p', p=product['product_id'])
    assert row is not None and row['product_status'] == 2


def test_staff_cannot_delete_customer(client, accounts):
    token = _login(client, accounts['staff_phone'])
    resp = client.post('/customer/delete', data={
        'customer_id': accounts['customer2'], 'csrf_token': token,
    })
    assert resp.status_code == 403


def test_staff_cannot_restore_customer(client, accounts):
    token = _login(client, accounts['staff_phone'])
    resp = client.post('/customer/restore', data={
        'customer_id': accounts['customer2'], 'csrf_token': token,
    })
    assert resp.status_code == 403


def test_customer_restore_reenables_account(client, test_engine, accounts):
    """注销是软删除，必须能撤销：cus_status 2→1，停用的账号 is_active 2→1。"""
    token = _login(client, accounts['admin_phone'])
    cid = accounts['customer2']
    phone = accounts['customer2_phone']

    client.post('/customer/delete', data={'customer_id': cid, 'csrf_token': token})
    assert _one(test_engine, 'SELECT cus_status FROM customers '
                             'WHERE customer_id = :c', c=cid)['cus_status'] == 2
    assert _one(test_engine, 'SELECT is_active FROM users WHERE phone = :p',
                p=phone)['is_active'] == 2

    resp = client.post('/customer/restore', data={
        'customer_id': cid, 'csrf_token': token,
    })
    assert resp.status_code == 302
    assert _one(test_engine, 'SELECT cus_status FROM customers '
                             'WHERE customer_id = :c', c=cid)['cus_status'] == 1
    assert _one(test_engine, 'SELECT is_active FROM users WHERE phone = :p',
                p=phone)['is_active'] == 1

    # 重复恢复：已经是正常状态，不报错也不改动数据
    resp = client.post('/customer/restore', data={
        'customer_id': cid, 'csrf_token': token,
    })
    assert resp.status_code == 302
    assert _one(test_engine, 'SELECT cus_status FROM customers '
                             'WHERE customer_id = :c', c=cid)['cus_status'] == 1


def test_customer_delete_is_soft_and_disables_account(client, test_engine, accounts):
    token = _login(client, accounts['admin_phone'])
    before_orders = _count(test_engine,
                           'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                           c=accounts['customer2'])

    resp = client.post('/customer/delete', data={
        'customer_id': accounts['customer2'], 'csrf_token': token,
    })
    assert resp.status_code == 302

    try:
        customer = _one(test_engine, 'SELECT cus_status FROM customers '
                                     'WHERE customer_id = :c', c=accounts['customer2'])
        assert customer['cus_status'] == 2
        user = _one(test_engine, 'SELECT is_active FROM users WHERE phone = :p',
                    p=accounts['customer2_phone'])
        assert user['is_active'] == 2
        # 订单是财务凭证：客户注销后一笔都不能少
        after_orders = _count(test_engine,
                              'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                              c=accounts['customer2'])
        assert before_orders == after_orders
    finally:
        # 本文件共用模块级 accounts，测完把客户二恢复，避免影响后面的用例
        with test_engine.begin() as conn:
            conn.execute(text('UPDATE customers SET cus_status = 1 '
                              'WHERE customer_id = :c'), {'c': accounts['customer2']})
            conn.execute(text('UPDATE users SET is_active = 1 WHERE phone = :p'),
                         {'p': accounts['customer2_phone']})


# ============================================================ 四、下单与数据隔离

def test_customer_can_create_order_via_form(client, test_engine, accounts):
    token = _login(client, accounts['customer1_phone'])

    # 1. 打开下单页：服务端把一次性 token 写进 session
    assert client.get('/order/create').status_code == 200
    with client.session_transaction() as sess:
        form_token = sess['_order_form_token']

    before = _count(test_engine,
                    'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                    c=accounts['customer1'])
    # 2. 提交：故意把 ppu/amount/customer_id 也传上，验证服务端全部忽略
    resp = client.post('/order/create', data={
        'product_id': 'SKU-test-1', 'quantity': '3',
        'ppu': '0.01', 'amount': '0.01',
        'customer_id': accounts['customer2'],
        'form_token': form_token, 'csrf_token': token,
    })
    assert resp.status_code == 302 and '/order/list' in resp.headers['Location']

    after = _count(test_engine,
                   'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                   c=accounts['customer1'])
    assert after == before + 1

    order = _one(test_engine, 'SELECT * FROM orders WHERE customer_id = :c '
                              'ORDER BY order_id DESC LIMIT 1',
                 c=accounts['customer1'])
    assert order['customer_id'] == accounts['customer1']   # 归属来自 session
    assert order['quantity'] == 3
    assert order['ppu'] == Decimal('12.50')                # 单价来自数据库
    assert order['amount'] == Decimal('37.50')             # 金额由服务端算
    assert order['order_status'] == 1


def test_order_form_double_submit_only_creates_one(client, test_engine, accounts):
    token = _login(client, accounts['customer1_phone'])
    client.get('/order/create')
    with client.session_transaction() as sess:
        form_token = sess['_order_form_token']

    before = _count(test_engine,
                    'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                    c=accounts['customer1'])
    payload = {'product_id': 'SKU-test-1', 'quantity': '1',
               'form_token': form_token, 'csrf_token': token}
    assert client.post('/order/create', data=payload).status_code == 302
    resp = client.post('/order/create', data=payload)     # 同一个 token 再提交一次
    assert resp.status_code == 302
    after = _count(test_engine,
                   'SELECT COUNT(*) FROM orders WHERE customer_id = :c',
                   c=accounts['customer1'])
    assert after == before + 1, '一次性 token：双击只允许生成一笔订单'


def test_customer_order_list_only_contains_own_orders(client, accounts, db):
    mine = create_order(accounts['customer1'], 'SKU-test-1', 1)
    others = create_order(accounts['customer2'], 'SKU-test-1', 1)

    token = _login(client, accounts['customer1_phone'])
    resp = client.get('/order/list')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '#%s' % mine['order_id'] in html
    assert others['transaction_id'] not in html, '客户不能看到别人的订单'


def test_customer_cannot_update_other_customers_order(client, accounts, db):
    others = create_order(accounts['customer2'], 'SKU-test-1', 1)
    token = _login(client, accounts['customer1_phone'])

    # 打开编辑页 -> 被重定向回列表（提示"订单不存在"）
    resp = client.get(f"/order/update?order_id={others['order_id']}")
    assert resp.status_code == 302

    # 强行 POST -> 也不改
    resp = client.post(f"/order/update?order_id={others['order_id']}",
                       data={'quantity': '9', 'csrf_token': token})
    assert resp.status_code == 302
    row = _one(db, 'SELECT quantity FROM orders WHERE order_id = :oid',
               oid=others['order_id'])
    assert row['quantity'] == 1


def test_customer_update_and_cancel_own_order(client, accounts, db):
    order = create_order(accounts['customer1'], 'SKU-test-1', 1)
    token = _login(client, accounts['customer1_phone'])

    assert client.get(f"/order/update?order_id={order['order_id']}").status_code == 200
    resp = client.post(f"/order/update?order_id={order['order_id']}",
                       data={'quantity': '4', 'csrf_token': token})
    assert resp.status_code == 302
    assert _one(db, 'SELECT quantity FROM orders WHERE order_id = :oid',
                oid=order['order_id'])['quantity'] == 4

    resp = client.post('/order/delete', data={
        'order_id': order['order_id'], 'csrf_token': token,
    })
    assert resp.status_code == 302
    assert _one(db, 'SELECT order_status FROM orders WHERE order_id = :oid',
                oid=order['order_id'])['order_status'] == 3


# ============================================================ 五、支付闭环

def test_staff_can_confirm_offline_payment(client, db, accounts):
    order = create_order(accounts['customer1'], 'SKU-test-1', 2)
    token = _login(client, accounts['staff_phone'])

    resp = client.post(f"/pay/confirm/{order['order_id']}",
                       data={'csrf_token': token})
    assert resp.status_code == 302

    row = _one(db, 'SELECT order_status, paid_at, paid_by FROM orders '
                   'WHERE order_id = :oid', oid=order['order_id'])
    assert row['order_status'] == 2
    assert row['paid_at'] is not None
    assert row['paid_by'] == _staff_id(db)


def test_customer_cannot_confirm_payment(client, db, accounts):
    order = create_order(accounts['customer1'], 'SKU-test-1', 1)
    token = _login(client, accounts['customer1_phone'])
    resp = client.post(f"/pay/confirm/{order['order_id']}",
                       data={'csrf_token': token})
    assert resp.status_code == 403


def test_pay_interfaces_are_idor_safe(client, db, accounts):
    """客户二去查/取消客户一的订单：一律 404，且订单状态不变。"""
    victim_order = create_order(accounts['customer1'], 'SKU-test-1', 1)
    token = _login(client, accounts['customer2_phone'])

    resp = client.get(f"/pay/order/{victim_order['order_id']}")
    assert resp.status_code == 404

    resp = client.post(f"/pay/cancel/{victim_order['order_id']}",
                       json={'request_id': 'idor-cancel-1'},
                       headers={'X-CSRF-Token': token})
    assert resp.status_code == 404
    assert _one(db, 'SELECT order_status FROM orders WHERE order_id = :oid',
                oid=victim_order['order_id'])['order_status'] == 1

    resp = client.post(f"/pay/prepay/{victim_order['order_id']}",
                       json={'request_id': 'idor-prepay-1'},
                       headers={'X-CSRF-Token': token})
    assert resp.status_code == 404


# ============================================================ 六、导入守卫

def test_import_is_blocked_once_business_data_exists(test_engine, prepared, accounts):
    """系统里已经有客户账号/手工商品/网页订单时，全量导入必须拒绝且不改数据。"""
    before = {
        'customers': _count(test_engine, 'SELECT COUNT(*) FROM customers'),
        'orders': _count(test_engine, 'SELECT COUNT(*) FROM orders'),
    }
    with pytest.raises(BusinessDataGuardError) as excinfo:
        with test_engine.begin() as conn:
            write_to_db(prepared, conn)      # 守卫在 DELETE 之前抛异常 -> 事务回滚
    assert '已中止' in str(excinfo.value)

    assert _count(test_engine, 'SELECT COUNT(*) FROM customers') == before['customers']
    assert _count(test_engine, 'SELECT COUNT(*) FROM orders') == before['orders']