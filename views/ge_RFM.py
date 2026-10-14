"""RFM 客户分层分析的 HTTP 入口。

这一层只做两件事：解析请求、渲染模板。
数据的清洗与导入在 utils.quality / utils.loader，不在这里。
"""
import pandas as pd
from flask import Blueprint, render_template, request

from utils import db

ge_RFM_blue = Blueprint('ge_RFM', __name__)


@ge_RFM_blue.route('/ge_rfm')
def ge_rfm():
    # 三次读取必须收进同一个事务：传 engine 时每次调用各自开事务，
    # 导入的 COMMIT 落在两次读取之间会让这个页面变成"半旧半新"
    # （客户表读到旧的、订单表读到新的，后面的 inner join 会静默丢掉数据）
    with db.engine.connect() as conn:
        customers_df = pd.read_sql('SELECT * FROM customers', conn, index_col=None)
        products_df = pd.read_sql('SELECT * FROM products', conn, index_col=None)
        orders_df = pd.read_sql('SELECT * FROM orders', conn, index_col=None)

    orders_df['order_date'] = pd.to_datetime(orders_df['order_date'])
    orders_df['max_date'] = orders_df['order_date'].max()
    orders_df['year'] = orders_df['order_date'].dt.year
    orders_df['r_day'] = (orders_df['max_date'] - orders_df['order_date']).dt.days

    df = pd.merge(customers_df, orders_df, on=['customer_id'], how='inner')
    rfm_df = df.groupby(['customer_id', 'year'], as_index=False).agg(
        r_day=('r_day', 'min'),
        quantity=('transaction_id', 'count'),   # F 是消费频次（交易笔数），不是购买件数
        amount=('amount', 'sum')
    )

    rfm_df['r_leval'] = pd.qcut(rfm_df['r_day'], 3, labels=['3', '2', '1'])
    rfm_df['f_leval'] = pd.cut(
        rfm_df['quantity'],
        bins=[0, 1, 3, float('inf')],
        labels=['1', '2', '3']
    )
    rfm_df['m_leval'] = pd.qcut(rfm_df['amount'], 3, labels=['1', '2', '3'])

    rfm_df['rfm_group'] = (rfm_df['r_leval'].astype(str) +
                           rfm_df['f_leval'].astype(str) +
                           rfm_df['m_leval'].astype(str))

    rfm_df = rfm_df.rename(columns={'r_day': 'r', 'quantity': 'f', 'amount': 'm'})

    years = sorted(rfm_df['year'].unique().tolist())

    selected_year = request.args.get('year', type=int)
    if selected_year:
        rfm_df = rfm_df[rfm_df['year'] == selected_year]

    datalist = rfm_df.to_dict(orient='records')

    return render_template('RFM_list.html',
                           datalist=datalist,
                           years=years,
                           selected_year=selected_year)