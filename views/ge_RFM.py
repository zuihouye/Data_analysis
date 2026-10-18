"""RFM 客户分层分析的 HTTP 入口。

这一层只做两件事：解析请求、渲染模板。
RFM 的计算口径在 utils/rfm.py，数据的清洗与导入在 utils/quality、utils/loader。
"""
import pandas as pd
from flask import Blueprint, render_template, request
from .auth import login_required
from utils import db
from utils.rfm import DEFAULT_WINDOW, WINDOWS, rfm_table, window_options

ge_RFM_blue = Blueprint('ge_RFM', __name__)


@ge_RFM_blue.route('/ge_rfm')
@login_required
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

    rfm_df, meta = rfm_table(customers_df, orders_df, window)

    return render_template('RFM_list.html',
                           datalist=rfm_df.to_dict(orient='records'),
                           windows=window_options(),
                           selected_window=window,
                           meta=meta)