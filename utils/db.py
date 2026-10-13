from sqlalchemy import create_engine,text
import pandas as pd
import numpy as np
import os


engine = create_engine('mysql+pymysql://root:15599544930@localhost/taobao_user?charset=utf8',
                       pool_recycle=3600,
                       pool_pre_ping=True,
                       echo=False,
                       pool_size=30,
                       max_overflow=20,
                       pool_timeout=30,)

# 连接数据库并进行数据清理
def process_and_import(csv_path='E:/Flask_app/static/archive/raw_rfm_sales_transactions_(V2).csv'):
    # 准备写入前，先清空旧数据（防止主键冲突）
    with engine.begin() as conn:
        # 关闭外键检查
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        conn.execute(text("TRUNCATE TABLE orders;"))
        conn.execute(text("TRUNCATE TABLE customers;"))
        conn.execute(text("TRUNCATE TABLE products;"))
        # 恢复外键检查
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))

    # 1. 读取 Excel 并清洗
    df = pd.read_csv(csv_path)

    # 清洗日期
    df['Date'] = pd.to_datetime(df['Date'],format='%d-%m-%Y').dt.date
    # 清洗金额
    df['PPU'] = df['PPU'].astype(str).str.replace(',','').astype(float)
    df['Amount'] = df['Amount'].astype(str).str.replace(',','').astype(float)

    df = df.dropna()

    # 2. 拆分三张表
    customers_df = df[['Customer ID','Region']].drop_duplicates(subset=['Customer ID','Region'])
    products_df = df[['Product ID','Product Name','Product Category','PPU']].drop_duplicates(subset=['Product ID'])
    orders_df = df[['Transaction ID','Date','Customer ID','Product ID','Quantity', 'PPU', 'Amount']]

    # 3.修改列名对应sql数据库
    customers_df = customers_df.rename(columns={
        'Customer ID': 'customer_id',
        'Region': 'region'
    })
    products_df = products_df.rename(columns={
        'Product ID': 'product_id',
        'Product Name': 'product_name',
        'Product Category': 'product_category',
        'PPU': 'ppu',

    })
    orders_df = orders_df.rename(columns={
        'Transaction ID': 'transaction_id',
        'Date': 'order_date',
        'Customer ID': 'customer_id',
        'Product ID': 'product_id',
        'Quantity': 'quantity',
        'PPU': 'ppu',
        'Amount': 'amount'
    })

    # 4. 写入数据库
    customers_df.to_sql('customers', engine, if_exists='append', index=False)
    products_df.to_sql('products', engine, if_exists='append', index=False)
    orders_df.to_sql('orders', engine, if_exists='append', index=False)

    return df