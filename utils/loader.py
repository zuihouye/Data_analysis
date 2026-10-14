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


def write_to_db(df, conn):
    """
    在调用方的事务中写库。

    刻意不用 CREATE / TRUNCATE / RENAME 等 DDL，也刻意不碰 FOREIGN_KEY_CHECKS：
    它们都会隐式提交，让外层事务失去回滚能力。这里只用 DML（DELETE + INSERT），
    表结构一行不动，失败可完整回滚。
    """
    customers_df = (df[['customer_id', 'region']]
                    .drop_duplicates(subset=['customer_id']))
    products_df = (df[['product_id', 'product_name', 'product_category', 'ppu']]
                   .drop_duplicates(subset=['product_id']))
    orders_df = df[['transaction_id', 'order_date', 'customer_id',
                    'product_id', 'quantity', 'ppu', 'amount']]

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
                       db_engine=None):
    """
    读取 CSV -> 清洗 -> 校验 -> 事务内写入。

    返回 (df, report)；数据质量不达标时抛 DataQualityError，此时数据库保持原样。

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
        write_to_db(df, conn)

    logger.info('导入完成: %d 行', len(df))
    return df, report