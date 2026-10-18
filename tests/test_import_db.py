"""
集成测试：write_to_db / process_and_import / /clear_input 路由。

全程只在一个临时数据库 taobao_user_test 里跑，跑完 DROP，
绝不碰真正的 taobao_user。MySQL 连不上时整组自动 skip。
"""
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from config import Config
from utils import db as dbmod          # 仅用于 monkeypatch engine，见文件末尾说明
from utils import loader
from utils.loader import process_and_import, write_to_db
from utils.quality import DataQualityError, clean, validate

CSRF_TOKEN = 'test-csrf-token'
ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / 'schema.sql'
CSV = ROOT / 'static' / 'archive' / 'raw_rfm_sales_transactions_(V2).csv'
TEST_DB = 'taobao_user_test'


def _uri(database=''):
    auth = f'{Config.DB_USER}:{quote_plus(Config.DB_PASSWORD)}'
    return (f'mysql+pymysql://{auth}@{Config.DB_HOST}:{Config.DB_PORT}'
            f'/{database}?charset=utf8mb4')


def _statements():
    """读 schema.sql，去掉注释行，拆成可直接执行的语句"""
    lines = [ln for ln in SCHEMA_SQL.read_text(encoding='utf-8').splitlines()
             if not ln.strip().startswith('--')]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()


def _ddl(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f'SHOW CREATE TABLE {table}')).fetchone()[1]


def _users(engine):
    """账号表快照，用于断言导入没有动它"""
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            'SELECT phone, username, password_hash, role FROM users '
            'ORDER BY id')).fetchall()]


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope='module')
def test_engine():
    server = create_engine(_uri())
    try:
        with server.begin() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {TEST_DB}'))
            conn.execute(text(f'CREATE DATABASE {TEST_DB} '
                              f'DEFAULT CHARACTER SET utf8mb4'))
    except Exception as exc:                    # MySQL 没起 -> 跳过，不算失败
        server.dispose()
        pytest.skip(f'MySQL 不可用，跳过集成测试: {type(exc).__name__}')

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
    """真实 CSV 经过 clean + validate，过滤掉脏行 —— 就是 process_and_import 会写库的那份"""
    report, mask = validate(clean(pd.read_csv(CSV)))
    assert not report['fatal']
    return clean(pd.read_csv(CSV))[~mask].reset_index(drop=True)


@pytest.fixture(scope='module')
def loaded(test_engine, prepared):
    """把数据灌进临时库，供后续用例断言"""
    with test_engine.begin() as conn:
        write_to_db(prepared, conn)
    return prepared


# ------------------------------------------------------------------ 用例

def test_write_to_db_populates_three_tables(test_engine, prepared, loaded):
    assert _count(test_engine, 'customers') == prepared['customer_id'].nunique()
    assert _count(test_engine, 'products') == prepared['product_id'].nunique()
    assert _count(test_engine, 'orders') == len(prepared)


def test_import_preserves_schema(test_engine, prepared, loaded):
    """
    回归测试：to_sql(if_exists='replace') 会按 DataFrame 的 dtype 重建表，
    丢掉主键 / UNIQUE / 外键 / 索引 / DECIMAL 精度。
    这条测试就是为了锁死这一点 —— 它曾经真实地被踩到过。
    """
    ddl = _ddl(test_engine, 'orders')
    assert 'PRIMARY KEY' in ddl
    assert 'UNIQUE KEY `transaction_id`' in ddl
    assert 'FOREIGN KEY' in ddl
    assert 'AUTO_INCREMENT' in ddl
    assert 'idx_order_date' in ddl and 'idx_customer' in ddl and 'idx_product' in ddl
    assert 'decimal(10,2)' in ddl, 'ppu 精度被改写了'
    assert 'decimal(12,2)' in ddl, 'amount 精度被改写了'
    assert '`order_date` date' in ddl, 'order_date 被改成了别的类型'


def test_duplicate_transaction_id_rejected(test_engine, loaded):
    """UNIQUE 还在就能拦住。表被重建时这条会挂"""
    with test_engine.connect() as conn:
        tid = conn.execute(text('SELECT transaction_id FROM orders LIMIT 1')).scalar()
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            conn.execute(text(
                'INSERT INTO orders (transaction_id, order_date, customer_id,'
                ' product_id, quantity, ppu, amount)'
                " VALUES (:t, '2025-03-03', 'Customer-002', 'PROD-001', 1, 100, 100)"),
                {'t': tid})


def test_foreign_key_still_enforced(test_engine, loaded):
    """外键被破坏时这条会挂"""
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            conn.execute(text(
                'INSERT INTO orders (transaction_id, order_date, customer_id,'
                ' product_id, quantity, ppu, amount)'
                " VALUES ('T-FK', '2025-03-03', '不存在的客户', 'PROD-001', 1, 100, 100)"))


def test_failure_inside_transaction_rolls_back(test_engine, prepared, loaded):
    """核心保证：写入中途失败，库必须保持原样，不能留下半灌状态"""
    before = _count(test_engine, 'orders')

    with pytest.raises(RuntimeError):
        with test_engine.begin() as conn:
            write_to_db(prepared, conn)   # 先 DELETE 再 INSERT
            raise RuntimeError('模拟写库中途失败')

    assert _count(test_engine, 'orders') == before, \
        '事务未回滚 —— DELETE 的效果被提交了'


def test_import_is_idempotent(test_engine, prepared, loaded):
    """重复导入同一份文件，结果应该一致，而不是数据叠加"""
    before = _count(test_engine, 'orders')
    with test_engine.begin() as conn:
        write_to_db(prepared, conn)
    assert _count(test_engine, 'orders') == before


def test_bad_quality_csv_leaves_database_intact(test_engine, loaded, tmp_dir):
    """质量不达标时应在写库之前中止，数据库保持原样"""
    before = {t: _count(test_engine, t) for t in ('customers', 'products', 'orders')}

    # 构造一份"交易号全重复"的 CSV -> 丢弃率 100%，必触发阈值保护
    bad = pd.read_csv(CSV)
    bad['Transaction ID'] = 'X'
    bad_path = tmp_dir / 'bad.csv'
    bad.to_csv(bad_path, index=False)

    with pytest.raises(DataQualityError):
        process_and_import(csv_path=bad_path, db_engine=test_engine)

    after = {t: _count(test_engine, t) for t in ('customers', 'products', 'orders')}
    assert after == before, '导入被拒绝，但数据库已经被改动了'


def test_process_and_import_end_to_end(test_engine):
    """走完整主流程：CSV -> clean -> validate -> 写库"""
    df, report = process_and_import(csv_path=CSV, db_engine=test_engine)

    assert len(df) == report['valid'] == report['total'] - report['dropped']
    assert _count(test_engine, 'orders') == len(df)
    assert report['drop_index'] == sorted(report['drop_index'])
    assert report['dropped'] == 1, 'V2 数据里有 1 条业务级重复'


# ------------------------------------------------------- 账号表与客户表解耦

def test_customers_has_no_login_columns(test_engine, loaded):
    """
    客户表不该装登录凭据 —— 账号在独立的 users 表里。

    历史：role / password_hash 一度被加在 customers 上，结果 /clear_input
    （全量重灌，先 DELETE FROM customers）每跑一次就把所有人的密码和角色
    冲成默认值。这条测试守着那次拆分。
    """
    ddl = _ddl(test_engine, 'customers')
    assert 'password_hash' not in ddl
    assert 'role' not in ddl
    assert 'cus_status' in ddl, '客户自己的状态列应该留着'


def test_users_phone_is_unique(test_engine, loaded):
    """手机号是登录标识，必须唯一；否则同一手机号能注册两次，登录时拿到哪条看运气"""
    with test_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (phone, username, password_hash) "
            "VALUES ('13900000001', 'bob', 'h1')"))

    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (phone, username, password_hash) "
                "VALUES ('13900000001', 'bob2', 'h2')"))


def test_import_does_not_touch_users(test_engine, prepared, loaded):
    """
    核心保证：导入客户数据【完全不影响】账号表。

    两者的数据生命周期互斥 —— customers 来自 CSV 是全量快照，
    users 来自用户手输是增量数据。这条测试把"两张表解耦"锁死：
    一旦有人把 users 挂上 customers 的外键（导入会报 1451），
    或把凭据挪回 customers（导入会冲掉密码），这里就会红。
    """
    with test_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (phone, username, password_hash, role) "
            "VALUES ('13800000000', 'alice', '$2b$12$fakehash', 2)"))

    before = _users(test_engine)
    assert ('13800000000', 'alice', '$2b$12$fakehash', 2) in before

    with test_engine.begin() as conn:
        write_to_db(prepared, conn)          # ← 点一次"导入数据"

    assert _users(test_engine) == before, \
        '导入改动了账号表 —— users 和 customers 没有真正解耦'


# ------------------------------------------------------------------ 路由层

def test_clear_input_route_returns_serializable_json(test_engine, loaded, monkeypatch):
    """
    把路由层也锁住。

    report 和 data_preview 里混进 Timestamp / ndarray 就会让 jsonify 抛
    TypeError；而 Timestamp 直接交给 Flask 会被序列化成 RFC 822 格式
    （'Wed, 01 Jan 2025 00:00:00 GMT'），前端拿到没法用。
    """
    # 把 utils.db.engine 指向临时库。
    # 这能生效的前提是 loader 里写的是 `db.engine` 而不是
    # `from utils.db import engine` —— 后者会把 engine 绑死在 loader 的
    # 模块命名空间里，monkeypatch 打不到。这条测试顺带守着那个约束。
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        # 模拟"管理员已登录 + 带合法 CSRF token"
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['phone'] = '13000000000'
            sess['role'] = 2
            sess['_csrf_token'] = CSRF_TOKEN

        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})
    assert resp.status_code == 200

    body = resp.get_json()
    assert body['status'] == 'success'
    assert body['total_rows'] == len(loaded)
    assert body['quality_report']['total'] == 2000
    assert body['quality_report']['dropped'] == 1
    assert len(body['data_preview']) == 20

    # 日期必须是 'YYYY-MM-DD'
    assert body['data_preview'][0]['order_date'] == '2025-01-01'
    assert all(len(r['order_date']) == 10 for r in body['data_preview'])


def test_clear_input_route_reports_quality_failure(test_engine, loaded, monkeypatch, tmp_dir):
    """质量不达标时路由应返回 400 + error，而不是 500 或者静默成功"""
    bad = pd.read_csv(CSV)
    bad['Transaction ID'] = 'X'                 # 交易号全重复 -> 触发阈值保护
    bad_path = tmp_dir / 'bad_route.csv'
    bad.to_csv(bad_path, index=False)

    monkeypatch.setattr(dbmod, 'engine', test_engine)
    # DEFAULT_CSV 住在 loader 里，所以要打在 loader 上而不是 dbmod 上
    monkeypatch.setattr(loader, 'DEFAULT_CSV', bad_path)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    before = _count(test_engine, 'orders')

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 2
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body['status'] == 'error'
    assert '数据质量不达标' in body['message']
    assert _count(test_engine, 'orders') == before, '被拒绝的导入不该改动数据库'

# ------------------------------------------------------- 安全层（P0-2 新增）

def test_write_without_csrf_token_is_rejected(test_engine, monkeypatch):
    """没有 CSRF token 的写请求，必须在进视图之前就被挡掉"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    resp = app.test_client().post('/clear_input')
    assert resp.status_code == 400
    assert 'CSRF' in resp.get_data(as_text=True)


def test_anonymous_write_goes_to_login(test_engine, monkeypatch):
    """token 合法但没登录 -> 302 跳登录页（而不是 400）"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})

    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_non_admin_write_is_forbidden(test_engine, monkeypatch):
    """普通成员（role=1）不能触发导入 —— 认证解决"你是谁"，授权解决"你能干什么" """
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['role'] = 1
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})

    assert resp.status_code == 403


def test_login_ignores_client_supplied_role(test_engine, monkeypatch):
    """客户端谎报 role=2 也没用：角色只认数据库里的值"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from sqlalchemy import text as sql_text
    from werkzeug.security import generate_password_hash

    with test_engine.begin() as conn:
        conn.execute(sql_text(
            'INSERT INTO users (phone, username, password_hash, role) '
            "VALUES ('13700000001', '普通成员小李', :h, 1)"),
            {'h': generate_password_hash('secret123')})

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/login', data={
            'mobile': '13700000001',
            'pwd': 'secret123',
            'role': '2',                 # ← 谎报管理员
            'csrf_token': CSRF_TOKEN,
        })
        assert resp.status_code == 302   # 登录成功
        with client.session_transaction() as sess:
            assert sess['role'] == 1     # 但角色仍是库里的 1
def test_login_then_import_end_to_end(test_engine, loaded, monkeypatch):
    """
    端到端：打开登录页 → 登录 → 打开业务页 → 导入成功。

    这条测试顺带锁住一个行为：登录会**轮换 CSRF token**（防会话固定攻击），
    所以登录之前渲染出来的旧表单会失效。浏览器登录后必然重新渲染页面、
    拿到新 token，正常流程无感 —— 但如果你把 token 写死在代码里，这里就会红。
    """
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from sqlalchemy import text as sql_text
    from werkzeug.security import generate_password_hash

    with test_engine.begin() as conn:
        conn.execute(sql_text(
            'INSERT INTO users (phone, username, password_hash, role) '
            "VALUES ('13700000002', '管理员老张', :h, 2)"),
            {'h': generate_password_hash('admin123')})

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        # 1. 打开登录页：页面渲染时会把 token 写进 session 和 HTML
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN

        # 2. 提交登录
        resp = client.post('/login', data={'mobile': '13700000002',
                                           'pwd': 'admin123',
                                           'csrf_token': CSRF_TOKEN})
        assert resp.status_code == 302

        # 3. 跟随跳转打开业务页（等价于浏览器重新渲染，layout_t.html 会重新出 token）
        resp = client.get('/customer/list')
        assert resp.status_code == 200, '登录后业务页应该能打开（顺带验证 layout 模板没写坏）'

        # 4. token 确实换过了
        with client.session_transaction() as sess:
            new_token = sess['_csrf_token']
        assert new_token != CSRF_TOKEN

        # 5. 用新 token 触发管理员操作
        resp = client.post('/clear_input', data={'csrf_token': new_token})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'success'
