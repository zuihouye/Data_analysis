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

    resp = app.test_client().get('/clear_input')
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
    resp = app.test_client().get('/clear_input')

    assert resp.status_code == 400
    body = resp.get_json()
    assert body['status'] == 'error'
    assert '数据质量不达标' in body['message']
    assert _count(test_engine, 'orders') == before, '被拒绝的导入不该改动数据库'