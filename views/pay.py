"""支付相关接口（JSON）+ 确认线下收款（页面表单）。

五条不变量（改这个文件前先读一遍）：
  0. 每个入口都要先做【归属校验】：客户只能访问自己的订单，员工/管理员不限。
     没有这一条就是 IDOR —— 客户登录后能取消别人的订单。
  1. 任何"读订单状态并根据它做决定"的操作，都必须先拿 order_lock(order_id)；
  2. 状态判断 + 状态修改必须在同一个事务里，用条件 UPDATE 的 rowcount 判定结果；
  3. 调第三方网关（网络 IO）必须放在事务之外；
  4. 金额只认数据库，绝不接受前端传入。

当前阶段：prepay 仍是 501 骨架（已决定不接真网关）；
真正的状态推进是"员工线下收款确认"：/pay/confirm/<order_id>，1 → 2。
"""
import logging

from flask import Blueprint, flash, jsonify, redirect, request, session, url_for
from sqlalchemy import text

from utils import db
from utils.idempotent import idempotent
from utils.lock import LockBusyError, order_lock
from utils.order_ops import (ORDER_PAID, ORDER_UNPAID, OrderError, cancel_order,
                             confirm_payment)

from .auth import login_required, staff_required
from .order import order_list_args

logger = logging.getLogger(__name__)

pay_blue = Blueprint('pay', __name__, url_prefix='/pay')


def _fetch_order(conn, order_id, for_update=False):
    """读订单。for_update=True 时加行锁（MySQL 上是 SELECT ... FOR UPDATE）。"""
    sql = ('SELECT order_id, transaction_id, customer_id, amount, order_status '
           'FROM orders WHERE order_id = :oid')
    if for_update:
        sql += ' FOR UPDATE'
    return conn.execute(text(sql), {'oid': order_id}).mappings().first()


def _can_access(order):
    """归属校验：客户只能访问自己的订单；员工/管理员不限。

    session 里的 role/customer_id 是登录时从数据库读出、由 SECRET_KEY 签名保护的，
    客户端改不了 —— 所以可以安全地作为授权依据。
    """
    role = session.get('role')
    if role in (1, 2):
        return True
    return role == 0 and order['customer_id'] == session.get('customer_id')


@pay_blue.route('/order/<int:order_id>', methods=['GET'])
@login_required
def order_detail(order_id):
    """查询订单可支付状态。前端轮询支付结果时也用它。"""
    with db.engine.begin() as conn:
        order = _fetch_order(conn, order_id)
    if order is None or not _can_access(order):
        # 别人的订单也返回 404：不让攻击者用"存在/不存在"的差异枚举订单
        return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
    return jsonify(ok=True, order=dict(order),
                   payable=(order['order_status'] == ORDER_UNPAID))


@pay_blue.route('/prepay/<int:order_id>', methods=['POST'])
@login_required
@idempotent('prepay')
def prepay(order_id):
    """发起支付：返回给前端的网关调起参数。

    本阶段刻意只回 501：接口契约、锁、幂等、状态校验、归属校验全部是真的，
    唯独"调网关"这一步没实现。这样前端可以先对接联调，也绝不会出现
    "假装支付成功"导致订单状态被错误推进的情况。
    """
    try:
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _fetch_order(conn, order_id, for_update=True)
                if order is None or not _can_access(order):
                    return jsonify(ok=False, code='ORDER_NOT_FOUND', msg='订单不存在'), 404
                if order['order_status'] == ORDER_PAID:
                    return jsonify(ok=False, code='ALREADY_PAID', msg='订单已支付'), 409
                if order['order_status'] != ORDER_UNPAID:
                    return jsonify(ok=False, code='ORDER_CLOSED', msg='订单已关闭，无法支付'), 409

                # 金额一律以数据库为准，绝不接受前端传入的金额
                params = {
                    'out_trade_no': order['transaction_id'],
                    'total_fee': str(order['amount']),
                }
            # 事务已结束（行锁已释放），下面才允许做网络 IO
            return jsonify(ok=False, code='PAY_NOT_IMPLEMENTED',
                           msg='支付网关尚未接入（演示环境）',
                           out_trade_no=params['out_trade_no']), 501
    except LockBusyError as e:
        return jsonify(ok=False, code='LOCK_BUSY', msg=str(e)), 409
    except Exception:
        logger.exception('发起支付失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500


@pay_blue.route('/cancel/<int:order_id>', methods=['POST'])
@login_required
@idempotent('cancel')
def cancel(order_id):
    """取消未支付订单（JSON 版）。

    真正的逻辑在 utils.order_ops.cancel_order：锁 + 条件 UPDATE + rowcount。
    页面表单 /order/delete 走的是同一个函数，两条入口行为一致。
    """
    try:
        cancel_order(order_id,
                     customer_id=session.get('customer_id'),
                     as_staff=session.get('role') in (1, 2))
    except OrderError as exc:
        status = {'ORDER_NOT_FOUND': 404, 'ALREADY_PAID': 409,
                  'ORDER_CLOSED': 409, 'LOCK_BUSY': 409}.get(exc.code, 409)
        return jsonify(ok=False, code=exc.code, msg=exc.msg), status
    except Exception:
        logger.exception('取消订单失败 order_id=%s', order_id)
        return jsonify(ok=False, code='INTERNAL', msg='系统繁忙，请稍后重试'), 500
    return jsonify(ok=True, cancelled=True)


@pay_blue.route('/confirm/<int:order_id>', methods=['POST'])
@staff_required
def confirm(order_id):
    """员工确认线下收款：订单 1 待付款 → 2 已支付。

    这是 B 方案的真实业务动作：不接网关，由员工核对转账/现金后点确认。
    paid_at / paid_by 同时落库，回答"谁在什么时候确认的"。
    """
    try:
        confirm_payment(order_id, staff_user_id=session['user_id'])
    except OrderError as exc:
        flash(exc.msg, 'danger')
    except Exception:
        logger.exception('确认收款失败 order_id=%s', order_id)
        flash('系统繁忙，请稍后重试', 'danger')
    else:
        flash(f'已确认收款：订单 #{order_id} 已标记为已支付', 'success')
    # 收款按钮在列表页的每行里，确认完回到原来那一页（分页后尤其重要）
    return redirect(url_for('order.order_list', **order_list_args(request.form)))