"""clean() / validate() 的单元测试。纯内存，不连数据库。"""
from pathlib import Path

import pandas as pd

from utils.quality import (COLUMN_MAP, REQUIRED_COLUMNS, clean, validate)

CSV = (Path(__file__).resolve().parent.parent
       / 'static' / 'archive' / 'raw_rfm_sales_transactions_(V2).csv')


def _row(**kw):
    """构造一行合法数据，再用 kw 覆盖想弄脏的字段"""
    base = dict(transaction_id='T1', order_date=pd.Timestamp('2025-01-01'),
                customer_id='C1', region='Yangon', product_id='P1',
                quantity=5, ppu=100.0, amount=500.0)
    return {**base, **kw}


# ---------------------------------------------------------------- clean

def test_clean_renames_columns_to_snake_case():
    """clean 的输出列名必须和 validate 的 REQUIRED_COLUMNS 对得上 —— 这条曾经断过"""
    raw = pd.read_csv(CSV)
    out = clean(raw)
    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    assert missing == [], f'clean 输出缺少 validate 需要的列: {missing}'
    assert set(COLUMN_MAP.values()) <= set(out.columns)


def test_clean_does_not_drop_rows():
    """clean 只转换、不删行。删谁由 validate 决定"""
    raw = pd.DataFrame({
        'Transaction ID': ['T1', 'T2'],
        'Date': ['01-01-2025', '不是日期'],       # 第二行日期非法
        'Customer ID': ['C1', 'C2'],
        'Region': ['Yangon', 'Bago'],
        'Product ID': ['P1', 'P1'],
        'Product Name': ['鼠标', '鼠标'],
        'Product Category': ['电子', '电子'],
        'Quantity': ['5', '3'],
        'PPU': ['1,000', '1,000'],
        'Amount': ['5,000', '3,000'],
    })
    out = clean(raw)
    assert len(out) == 2, 'clean 不应该删行'
    assert pd.isna(out.loc[1, 'order_date']), '非法日期应变成 NaT 而不是抛异常'


def test_clean_strips_thousands_separator():
    raw = pd.read_csv(CSV)
    out = clean(raw)
    assert out['amount'].max() == 600_000_000.0
    assert pd.api.types.is_numeric_dtype(out['amount'])


def test_clean_keeps_datetime_dtype():
    """不要用 .dt.date 把日期降级成 object"""
    raw = pd.read_csv(CSV)
    out = clean(raw)
    assert pd.api.types.is_datetime64_any_dtype(out['order_date'])


# ------------------------------------------------------------- validate

def test_validate_catches_each_issue():
    df = pd.DataFrame([
        _row(transaction_id='T1'),                                    # 正常
        _row(transaction_id='T2', amount=400.0),                      # 金额对不上
        _row(transaction_id='T1'),                                    # 交易号重复
        _row(transaction_id='T3'),                                    # 业务级重复(同 T1 内容)
        _row(transaction_id='T4', quantity=-1),                       # 负数量
        _row(transaction_id='T5', order_date=pd.Timestamp('2099-01-01')),   # 未来日期
        _row(transaction_id='T6', customer_id='C2', region='Bago'),   # 正常
        _row(transaction_id='T7', customer_id='C2', region='Mandalay'),     # 地区冲突
        _row(transaction_id='T8', ppu=None, amount=None),             # 空值
    ])
    report, mask = validate(df)

    assert {e['check'] for e in report['errors']} == {
        'null_values', 'duplicate_transaction_id',
        'business_duplicate', 'amount_mismatch', 'non_positive_value'}
    assert report['dropped'] == 6
    assert report['valid'] == 3
    # 两个出口必须一致，否则 process_and_import 会按错的集合过滤
    assert list(mask[mask].index) == report['drop_index']


def test_missing_column_is_fatal_and_short_circuits():
    report, mask = validate(pd.DataFrame({'transaction_id': ['T1'], 'amount': [1.0]}))
    assert report['fatal'], '缺少必需列必须走 fatal'
    assert report['dropped'] == 0
    assert report['valid'] == 0
    assert not mask.any()
    assert report['errors'] == [], 'fatal 时应短路，不再跑其它检查'


def test_business_duplicate_policy_is_configurable():
    """同一份数据，换策略应该改变处置方式而不是被静默忽略"""
    df = pd.DataFrame([_row(transaction_id='T1'), _row(transaction_id='T2')])

    hard, mask_hard = validate(df, dedupe_business_duplicates=True)
    soft, mask_soft = validate(df, dedupe_business_duplicates=False)

    assert [e['check'] for e in hard['errors']] == ['business_duplicate']
    assert mask_hard.sum() == 1
    # 这两行客户/日期/商品本来也一样，所以同天同商品警告也会亮 —— 符合预期
    assert 'same_day_same_product' in {w['check'] for w in hard['warnings']}
    # 策略关闭时：降级为 warning，且不丢弃
    assert 'business_duplicate' in {w['check'] for w in soft['warnings']}
    assert 'business_duplicate' not in {e['check'] for e in soft['errors']}
    assert mask_soft.sum() == 0


def test_amount_mismatch_ignores_nan():
    """np.isclose(nan, x) 是 False，不加 comparable 掩码会把空值行误报成金额不一致"""
    df = pd.DataFrame([_row(transaction_id='T1', ppu=None, amount=None)])
    report, _ = validate(df)
    checks = {e['check'] for e in report['errors']}
    assert 'null_values' in checks
    assert 'amount_mismatch' not in checks, '空值行不应该被误判为金额不一致'


def test_report_is_json_serializable():
    """报告要能直接丢给 jsonify —— 不能夹带 Timestamp / Decimal / ndarray"""
    import json
    raw = pd.read_csv(CSV)
    report, _ = validate(clean(raw))
    json.dumps(report)          # 抛 TypeError 就说明报告里混进了非 JSON 对象


def test_valid_data_has_no_errors():
    """两行完全干净且互不相似的数据，不该触发任何 error"""
    df = pd.DataFrame([
        _row(transaction_id='T1'),
        _row(transaction_id='T2', customer_id='C2', region='Bago',
             product_id='P2', order_date=pd.Timestamp('2025-02-01'),
             quantity=3, amount=300.0),
    ])
    report, mask = validate(df)
    assert report['errors'] == []
    assert report['dropped'] == 0
    assert not mask.any()


def test_outlier_is_warning_not_error():
    """离群值只警告、绝不丢弃 —— 业务上无法自动判断它是不是脏数据"""
    rows = [_row(transaction_id=f'T{i}', customer_id=f'C{i}', product_id=f'P{i}',
                 order_date=pd.Timestamp('2025-01-01') + pd.Timedelta(days=i),
                 quantity=i + 2, ppu=100.0, amount=(i + 2) * 100.0)
            for i in range(50)]
    # 追加一条极端值：quantity 与 amount 都远大，但保持 amount == quantity * ppu
    rows.append(_row(transaction_id='T-BIG', customer_id='C-BIG', product_id='P-BIG',
                     order_date=pd.Timestamp('2025-06-01'),
                     quantity=100_000, ppu=100.0, amount=10_000_000.0))

    report, mask = validate(pd.DataFrame(rows))
    checks = {w['check'] for w in report['warnings']}
    assert 'quantity_outlier' in checks
    assert 'amount_outlier' in checks
    assert report['errors'] == [], '离群值不应该变成 error'
    assert not mask.any(), '离群值不应该被丢弃'