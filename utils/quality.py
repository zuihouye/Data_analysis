"""数据质量：列名标准化、类型转换、质量校验。

这一层是**只读的判断层**：
  clean()    只做类型转换，不删任何行
  validate() 只检查，不修改 df

"该丢谁"在这里判断，"实际丢弃"由 utils.loader.process_and_import() 执行。
把这条边界守住，才能回答"删了多少、为什么删"。
"""
import numpy as np
import pandas as pd

# CSV 原始列名 -> 数据库列名
COLUMN_MAP = {
    'Transaction ID': 'transaction_id',
    'Date': 'order_date',
    'Customer ID': 'customer_id',
    'Region': 'region',
    'Product ID': 'product_id',
    'Product Name': 'product_name',
    'Product Category': 'product_category',
    'Quantity': 'quantity',
    'PPU': 'ppu',
    'Amount': 'amount',
}

REQUIRED_COLUMNS = ['transaction_id', 'order_date', 'customer_id', 'region',
                    'product_id', 'quantity', 'ppu', 'amount']

# 业务级重复判定键：这些列全同 => 高置信重复
BUSINESS_DUP_KEY = ['customer_id', 'order_date', 'product_id', 'quantity', 'ppu', 'amount']
SOFT_DUP_KEY = ['customer_id', 'order_date', 'product_id']


class DataQualityError(Exception):
    """数据质量不达标，拒绝导入"""


# ---------------------------------------------------------------------------
# 清洗：只做列名标准化 + 类型转换，不删除任何行
# ---------------------------------------------------------------------------

def clean(df):
    """
    列名标准化 + 类型转换。

    刻意不删任何行：该丢谁由 validate() 判断、由 loader 执行，
    这样"删了多少、为什么删"才可追溯。
    转换失败的值变成 NaT / NaN，交给 validate() 的 null_values 检查报告。
    """
    df = df.rename(columns=COLUMN_MAP)
    df['order_date'] = pd.to_datetime(df['order_date'],
                                      format='%d-%m-%Y', errors='coerce')
    for col in ('quantity', 'ppu', 'amount'):
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''),
                                errors='coerce')
    return df


# ---------------------------------------------------------------------------
# 校验：只检查、不修改 df
# ---------------------------------------------------------------------------

def _samples(df, mask, columns, limit=5):
    """取前 N 条问题行。必须 astype(str)，否则 date/Decimal 对象进不了 jsonify"""
    return df.loc[mask, columns].head(limit).astype(str).to_dict(orient='records')


def _iqr_outlier(s, k=1.5):
    """IQR 离群检测。不用 3σ —— 均值和标准差本身会被离群值污染"""
    q1, q3 = s.quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:                       # 常量列，无离群可言
        return pd.Series(False, index=s.index)
    return (s < q1 - k * iqr) | (s > q3 + k * iqr)


def validate(df, dedupe_business_duplicates=True):
    """
    数据质量校验。只检查、不修改 df。

    前置条件：已经过 clean()，列名是英文、类型已转换。

    返回 (report, drop_mask)
      report    : dict，JSON 可序列化，可直接丢给 jsonify
      drop_mask : pd.Series[bool]，True 表示该行应被丢弃

    严重级：
      fatal    —— 非空则表示应当拒绝整个导入
      errors   —— 该行应被丢弃
      warnings —— 保留，但需要关注
    """
    report = {'total': int(len(df)), 'fatal': [], 'errors': [], 'warnings': []}
    drop_mask = pd.Series(False, index=df.index)

    # ---------- 致命：必需列缺失（后面全依赖它，直接短路）----------
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        report['fatal'].append({'check': 'required_columns',
                                'detail': f'缺少必需列: {missing}',
                                'missing': missing})
        report.update(valid=0, dropped=0, drop_index=[])
        return report, drop_mask

    def add_error(check, mask, detail, cols):
        nonlocal drop_mask
        n = int(mask.sum())
        if n == 0:
            return
        drop_mask |= mask
        report['errors'].append({'check': check, 'count': n,
                                 'rate': round(n / len(df), 4),
                                 'detail': detail,
                                 'samples': _samples(df, mask, cols)})

    def add_warning(check, mask, detail, cols):
        n = int(mask.sum())
        if n == 0:
            return
        report['warnings'].append({'check': check, 'count': n,
                                   'rate': round(n / len(df), 4),
                                   'detail': detail,
                                   'samples': _samples(df, mask, cols)})

    # 1. 空值
    add_error('null_values', df[REQUIRED_COLUMNS].isna().any(axis=1),
              '必需字段存在空值（对应数据库 NOT NULL 列）', REQUIRED_COLUMNS)

    # 2. transaction_id 重复
    add_error('duplicate_transaction_id',
              df['transaction_id'].duplicated(keep='first'),
              'transaction_id 重复（orders.transaction_id 是 UNIQUE，会整批失败）',
              ['transaction_id', 'customer_id', 'order_date'])

    # 3. 业务级重复 —— 数据库完全拦不住
    biz_dup = df.duplicated(subset=BUSINESS_DUP_KEY, keep='first')
    if dedupe_business_duplicates:
        add_error('business_duplicate', biz_dup,
                  '内容完全相同但 transaction_id 不同 —— UNIQUE 拦不住，会静默重复计算金额',
                  BUSINESS_DUP_KEY)
    else:
        add_warning('business_duplicate', biz_dup,
                    '疑似业务级重复（当前策略：保留）', BUSINESS_DUP_KEY)

    # 4. 同客户同天同商品（置信度低，仅警告）
    add_warning('same_day_same_product',
                df.duplicated(subset=SOFT_DUP_KEY, keep=False) & ~biz_dup,
                '同一客户同一天购买同一商品多次（可能是真实复购，需业务确认）',
                SOFT_DUP_KEY + ['quantity', 'amount'])

    # 5. 金额一致性
    expected = df['quantity'] * df['ppu']
    comparable = expected.notna() & df['amount'].notna()
    close = np.isclose(df['amount'].to_numpy('float64'),
                       expected.to_numpy('float64'), rtol=1e-6)
    add_error('amount_mismatch',
              comparable & pd.Series(~close, index=df.index),
              'amount != quantity * ppu（无法推断哪个值对，绝不能自动修）',
              ['transaction_id', 'quantity', 'ppu', 'amount'])

    # 6. 非正数
    add_error('non_positive_value',
              (df['quantity'] <= 0) | (df['ppu'] <= 0) | (df['amount'] <= 0),
              'quantity / ppu / amount 存在 0 或负数',
              ['transaction_id', 'quantity', 'ppu', 'amount'])

    # 7. 未来日期
    add_warning('future_date',
                df['order_date'].notna() & (df['order_date'] > pd.Timestamp.today().normalize()),
                '订单日期晚于今天（可能是预付订单或时钟错误）',
                ['transaction_id', 'order_date'])

    # 8. 客户-地区一对多
    add_warning('customer_region_conflict',
                df.groupby('customer_id')['region'].transform('nunique') > 1,
                '同一客户出现在多个地区（customers.customer_id 是主键，会冲突）',
                ['customer_id', 'region'])

    # 9. 离群值（只警告，绝不丢弃）
    for col in ('quantity', 'amount'):
        add_warning(f'{col}_outlier', _iqr_outlier(df[col]),
                    f'{col} 超出 IQR 1.5 倍范围（保留，交业务判断）',
                    ['transaction_id', col])

    report['drop_index'] = df.index[drop_mask].tolist()
    report['dropped'] = int(drop_mask.sum())
    report['valid'] = report['total'] - report['dropped']
    return report, drop_mask