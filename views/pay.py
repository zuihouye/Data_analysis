"""支付相关接口。

三条不变量（改这个文件前先读一遍）：
  1. 任何"读取订单状态并根据它做决定"的操作，都必须先拿 order_lock(order_id)；
  2. 状态判断 + 状态修改必须在同一个事务里，且用条件 UPDATE 的 rowcount 判定结果，
     不允许写成「先 if 查出来的状态，再无条件 UPDATE」；
  3. 调第三方网关（网络 IO）必须放在事务之外，绝不放在持有行锁的 with 块里。

当前阶段：只落地"锁 + 幂等 + 状态机"骨架，不接真实支付网关。
真网关 / 回调 / 流水表见第 11 节 P1-B 待办。
"""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from utils import db
from utils.idempotent import idempotent
from utils.lock import LockBusyError, order_lock

logger = logging.getLogger(__name__)

pay_blue = Blueprint('pay', __name__, url_prefix='/pay')

# 订单状态，与 schema.sql 注释保持一致：1 已下单 / 2 已支付 / 3 已取消
ORDER_UNPAID, ORDER_PAID, ORDER_CANCELLED = 1, 2, 3

def _fetch_order(conn, order_id, for_update=False):
    """读订单。for_update=True 时加行锁（MySQL 上是 SELECT ... FOR UPDATE）。"""
    sql = ('SELECT order_id, transaction_id, amount, order_status '
           'FROM orders WHERE order_id = :oid')
    if for_update:
        sql += ' FOR UPDATE'
    return conn.execute(text(sql), {'oid': order_id}).mappings().first()


@pay_blue.route('/order/<int:order_id>', methods=['GET'])
def order_detail(order_id):
    """查询订单可支付状态。前端轮询支付结果时也用它。"""
    with db.engine.begin() as conn:
        order = _fetch_order(conn, order_id)
    if order is None:
        return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
    return jsonify(ok=True, order=dict(order),
                   payable=(order['order_status'] == ORDER_UNPAID))



@pay_blue.route('/prepay/<int:order_id>', methods=['POST'])
@idempotent('prepay')
def prepay(order_id):
    """发起支付：返回给前端的网关调起参数。

    本阶段刻意只回 501：接口契约、锁、幂等、状态校验全部是真的，
    唯独"调网关"这一步没实现。这样前端可以先对接联调，也绝不会出现
    "假装支付成功"导致订单状态被错误推进的情况。
    """
    try:
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _fetch_order(conn, order_id, for_update=True)
                if order is None:
                    return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
                if order['order_status'] == ORDER_PAID:
                    return jsonify(ok=False, code='ALREADY_PAID', msg='订单已支付'), 409
                if order['order_status'] != ORDER_UNPAID:
                    return jsonify(ok=False, code='ORDER_CLOSED', msg='订单已关闭，无法支付'), 409

                # 金额一律以数据库为准，绝不接受前端传入的金额 ——
                # 否则改一下请求体就能 1 元买手机。
                params = {
                    'out_trade_no': order['transaction_id'],
                    'total_fee': str(order['amount']),
                }
            # 事务已结束（行锁已释放），下面才允许做网络 IO
            return jsonify(ok=False, code='PAY_NOT_IMPLEMENTED',
                           msg='支付网关尚未接入（P1-B 待办）',
                           out_trade_no=params['out_trade_no']), 501
    except LockBusyError as e:
        # 409 而不是 500：这是"另一个请求正在处理"，客户端稍后重试即可
        return jsonify(ok=False, code='LOCK_BUSY', msg=str(e)), 409
    except Exception:
        logger.exception('发起支付失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500


@pay_blue.route('/cancel/<int:order_id>', methods=['POST'])
@idempotent('cancel')
def cancel(order_id):
    """取消未支付订单。
    这是本阶段**唯一真的会写库**的支付类接口，所以它同时充当
    "条件 UPDATE + rowcount" 这个模式的样板：
    UPDATE 带上 WHERE order_status = 1，改到 0 行就说明状态已经变了。
    """
    try:
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _fetch_order(conn, order_id, for_update=True)
                if order is None:
                    return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
                if order['order_status'] == ORDER_PAID:
                    return jsonify(ok=False, code='ALREADY_PAID',
                                   msg='订单已支付，请走退款流程'), 409

                result = conn.execute(text('''
                    UPDATE orders
                       SET order_status = :cancelled
                     WHERE order_id = :oid
                       AND order_status = :unpaid
                '''), {'cancelled': ORDER_CANCELLED,
                      'unpaid': ORDER_UNPAID,
                      'oid': order_id})

            if result.rowcount == 0:
                # 状态在别处已经变过了（并发取消 / 回调刚把它标成已支付）
                return jsonify(ok=False, code='STATE_CHANGED', msg='订单状态已变化，请刷新'), 409
            return jsonify(ok=True, cancelled=True)
    except LockBusyError as e:
        return jsonify(ok=False, code='LOCK_BUSY', msg=str(e)), 409
    except Exception:
        logger.exception('取消订单失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500