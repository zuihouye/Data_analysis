"""RFM 客户分层分析的 HTTP 入口。

这一层只做三件事：解析请求、取数、渲染模板。
RFM 的计算口径在 utils/rfm.py，页面聚合在 utils/rfm_report.py，
数据的清洗与导入在 utils/quality、utils/loader。
"""
import pandas as pd
from flask import Blueprint, render_template, request

from .auth import staff_required
from utils import db
from utils.rfm import DEFAULT_WINDOW, WINDOWS, rfm_table
from utils.rfm_report import build_report, detail_rows

ge_RFM_blue = Blueprint('ge_RFM', __name__)

# order_status: 1=已下单 / 2=已支付 / 3=已取消。
# RFM 只统计【已支付】的订单：未付款和已取消的单不该算进客户价值。
PAID_STATUS = 2


@ge_RFM_blue.route('/ge_rfm')
@staff_required
def ge_rfm():
    # 两次读取必须收进同一个事务：传 engine 时每次调用各自开事务，
    # 导入的 COMMIT 落在两次读取之间会让这个页面变成"半旧半新"
    # （客户表读到旧的、订单表读到新的），后面的 merge 会静默丢掉数据
    with db.engine.connect() as conn:
        customers_df = pd.read_sql('SELECT * FROM customers', conn, index_col=None)
        orders_df = pd.read_sql('SELECT * FROM orders', conn, index_col=None)

    # 参数白名单：拿脏 key 直接去查 WINDOWS 会 KeyError
    window = request.args.get('window', DEFAULT_WINDOW)
    if window not in WINDOWS:
        window = DEFAULT_WINDOW

    # 口径：只统计已支付订单。历史导入的流水在 utils/loader 里统一标记为已支付，
    # 网页下单要等员工确认收款后 order_status 才会变成 2。
    paid_orders = orders_df[orders_df['order_status'] == PAID_STATUS]

    rfm_df, meta = rfm_table(customers_df, paid_orders, window)
    report = build_report(customers_df, paid_orders, window,
                          rfm_df=rfm_df, meta=meta)

    return render_template(
        'RFM_list.html',
        meta=meta,
        report=report,
        detail=detail_rows(rfm_df, customers_df),
        orders_total=int(len(orders_df)),
        windows=[{'key': k, 'label': label} for k, (label, _) in WINDOWS.items()],
        selected_window=window,
    )
