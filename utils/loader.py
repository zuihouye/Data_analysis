"""把清洗、校验过的数据写进数据库。

只做"写入"这一件事：清洗和校验在 utils.quality 里。
整个写入只用 DML（DELETE + INSERT），不使用任何 DDL —— 详见 write_to_db 的说明。
"""
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from utils import db
from utils.quality import DataQualityError, clean, validate

logger = logging.getLogger(__name__)

# 默认 CSV 相对项目根目录定位，换机器不用改代码
DEFAULT_CSV = (Path(__file__).resolve().parent.parent
               / 'static' / 'archive' / 'raw_rfm_sales_transactions_(V2).csv')


class BusinessDataGuardError(RuntimeError):
    """系统内已有业务数据，全量导入会被拒绝（保护客户账号与财务订单）。"""


# 任何一项 > 0 都说明"库里有 CSV 之外产生的数据"：
#   1) role=0 的账号 = 有人注册过；
#   2) customers 里出现非 Customer- 前缀 = 注册客户；
#   3) products 里出现非 PROD- 前缀 = 员工手工加过商品；
#   4) orders 里出现非 T 前缀 = 有网页/线下下单。
_GUARD_SQL = '''
    SELECT
      (SELECT COUNT(*) FROM users     WHERE role = 0) AS customer_accounts,
      (SELECT COUNT(*) FROM customers WHERE customer_id NOT LIKE 'Customer-%') AS web_customers,
      (SELECT COUNT(*) FROM products  WHERE product_id  NOT LIKE 'PROD-%')     AS manual_products,
      (SELECT COUNT(*) FROM orders    WHERE transaction_id NOT LIKE 'T%')      AS web_orders
'''


def business_data_counts(conn=None):
    """返回四类"导入会误删"的业务数据计数，供导入页展示与二次确认。

    conn 为空时自己开一个只读连接（页面渲染用）；调用方在事务里时应传同一个 conn。
    """
    if conn is None:
        with db.engine.connect() as own:
            row = own.execute(text(_GUARD_SQL)).mappings().first()
    else:
        row = conn.execute(text(_GUARD_SQL)).mappings().first()
    return {k: int(v or 0) for k, v in row.items()}


def ensure_import_safe(conn):
    """在 DELETE 之前检查：库里有没有"导入会误删"的业务数据。

    必须传调用方的 conn（同一个事务），保证"检查 → 删除 → 写入"是一体的。
    只要有一项 > 0 就抛异常，让整个事务回滚 —— 宁可拒绝导入，也不删客户账号/财务订单。
    """
    row = conn.execute(text(_GUARD_SQL)).mappings().first()
    details = {k: v for k, v in row.items() if v}
    if details:
        raise BusinessDataGuardError(
            '检测到系统内已有业务数据（客户账号 / 注册客户 / 手工商品 / 网页订单），'
            f'全量导入会删除它们，已中止：{details}。'
            '可在导入页使用「清空并重建」（会删除客户账号、商品与订单，保留员工/管理员账号），'
            '或换一个新库导入。'
        )


def _clear_business_data(conn):
    """「清空并重建」的清理步骤，只在 force_clear=True 时调用。

    顺序有讲究：
      1. 先把 users 与 customers 的外键绑定解开，否则后面 DELETE customers 会报 1451；
      2. 客户账号（role=0）连人带档案一起删；员工/管理员账号保留，只解绑客户档案；
      3. 按子表 -> 父表顺序删 orders -> customers -> products，不需要关外键检查。
    """
    conn.execute(text(
        'UPDATE users SET customer_id = NULL WHERE customer_id IS NOT NULL'))
    conn.execute(text('DELETE FROM users WHERE role = 0'))
    for table in ('orders', 'customers', 'products'):
        conn.execute(text(f'DELETE FROM {table}'))

def write_to_db(df, conn, force_clear=False):
    """
    在调用方的事务中写库。

    刻意不用 CREATE / TRUNCATE / RENAME 等 DDL，也刻意不碰 FOREIGN_KEY_CHECKS：
    它们都会隐式提交，让外层事务失去回滚能力。这里只用 DML（DELETE + INSERT），
    表结构一行不动，失败可完整回滚。
    ...
    【P2 新增】若检测到业务数据会抛 BusinessDataGuardError，事务回滚。
    【本次新增】force_clear=True 时跳过保护检查，改为显式清空业务数据后再导入
    （导入页「清空并重建」按钮，需要前端二次确认）。
    """

    if force_clear:
        _clear_business_data(conn)
    else:
        ensure_import_safe(conn)


    customers_df = (df[['customer_id', 'region']]
                    .drop_duplicates(subset=['customer_id']))
    products_df = (df[['product_id', 'product_name', 'product_category', 'ppu']]
                   .drop_duplicates(subset=['product_id']))
    # 历史流水没有支付状态列，统一按「已支付」立账（order_status=2）：
    #   - 数据集本身就是历史成交记录，没有待付款/已取消概念；
    #   - RFM 只统计已支付订单，若不标记，导入后整张订单表都会被排除；
    #   - paid_at 留空（NULL），R 指标用 order_date 计算，不依赖支付时间。
    orders_df = df[['transaction_id', 'order_date', 'customer_id',
                    'product_id', 'quantity', 'ppu', 'amount']].copy()
    orders_df['order_status'] = 2

    # 按依赖顺序删：先子表 orders，再父表 —— 因此无需关闭外键检查
    for table in ('orders', 'customers', 'products'):
        conn.execute(text(f'DELETE FROM {table}'))

    # 按依赖顺序写：先父表后子表，否则外键校验会失败
    # to_sql 传 Connection 时会纳入外层事务，不自己提交
    customers_df.to_sql('customers', conn, if_exists='append', index=False)
    products_df.to_sql('products', conn, if_exists='append', index=False)
    orders_df.to_sql('orders', conn, if_exists='append', index=False)

    logger.info('写入完成: customers=%d products=%d orders=%d',
                len(customers_df), len(products_df), len(orders_df))


def process_and_import(csv_path=None,
                       dedupe_business_duplicates=True,
                       max_drop_rate=0.2,
                       db_engine=None,
                       force_clear=False):
    """
    读取 CSV -> 清洗 -> 校验 -> 事务内写入。

    返回 (df, report)；数据质量不达标时抛 DataQualityError，此时数据库保持原样。
    force_clear=True 表示已获得「清空并重建」的显式授权（跳过业务数据保护）。

    db_engine 仅用于测试时指向临时库，生产调用不要传。
    """
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV
    logger.info('开始导入: %s', csv_path)

    df = clean(pd.read_csv(csv_path))
    report, drop_mask = validate(df, dedupe_business_duplicates)

    # 致命问题：列都缺了，后面全无意义
    if report['fatal']:
        raise DataQualityError(report['fatal'][0]['detail'])

    # 阈值保护：脏数据太多说明文件本身有问题，整体拒绝而不是"导进去一部分"
    drop_rate = report['dropped'] / max(report['total'], 1)
    if drop_rate > max_drop_rate:
        raise DataQualityError(
            f'脏数据占比 {drop_rate:.1%} 超过阈值 {max_drop_rate:.0%}，'
            f'疑似文件错误，已中止导入'
        )
    if report['dropped']:
        logger.warning('丢弃 %d 行脏数据（占 %.2f%%）', report['dropped'], drop_rate * 100)

    df = df[~drop_mask].reset_index(drop=True)

    # 注意用 db.engine 而不是 from utils.db import engine：
    # 前者让测试可以 monkeypatch utils.db.engine 把写入重定向到临时库
    with (db_engine or db.engine).begin() as conn:   # 整个写入是一个事务
        write_to_db(df, conn, force_clear=force_clear)

    logger.info('导入完成: %d 行', len(df))
    return df, report