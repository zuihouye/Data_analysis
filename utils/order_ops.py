"""订单业务操作：下单、改数量、取消、确认收款。

分层：
    本模块 = 业务规则 + 数据库事务（不依赖 Flask）
    views 层 = 解析请求、权限、flash 提示、重定向

状态机（与 schema.sql 注释、1/2/3 对应）：
    1 已下单/待付款  --确认收款-->  2 已支付   （冻结：不可改、不可取消）
    1 已下单/待付款  --取消------>  3 已取消

每个写操作都遵循同一个模板（这是本项目最重要的并发套路）：
    with order_lock(order_id):              # 1. Redis 锁：同一订单串行化
        with db.engine.begin() as conn:     # 2. 一个事务里"读 + 判断 + 条件 UPDATE"
            SELECT ... FOR UPDATE           # 3. 行锁兜底（防锁过期/多实例）
            UPDATE ... WHERE order_status = 旧状态
        if result.rowcount == 0:            # 4. 谁改到了谁赢，输的一方报"状态已变化"
            raise OrderError('STATE_CHANGED', ...)
"""
import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from utils import db
from utils.lock import LockBusyError, order_lock

logger = logging.getLogger(__name__)

# 订单状态（与 schema.sql 的 1/2/3 一致）
ORDER_UNPAID, ORDER_PAID, ORDER_CANCELLED = 1, 2, 3

MAX_QUANTITY = 999


class OrderError(Exception):
    """业务规则不满足。views 层把它转成 flash 提示 / JSON 错误。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def _run_locked(fn):
    """把"等不到锁"翻译成统一的业务错误，views 层不用各自处理 LockBusyError。"""
    try:
        return fn()
    except LockBusyError:
        raise OrderError('LOCK_BUSY', '操作处理中，请稍后重试') from None


def get_order(order_id):
    """只读查询，不加锁（展示用）。"""
    with db.engine.connect() as conn:
        return conn.execute(text(
            'SELECT order_id, transaction_id, customer_id, product_id, '
            '       quantity, ppu, amount, order_status, paid_at '
            'FROM orders WHERE order_id = :oid'), {'oid': order_id}).mappings().first()


def create_order(customer_id, product_id, quantity):
    """客户下单。

    安全点：
      - customer_id 由 views 从 session 传进来，绝不来自表单；
      - 单价只从数据库读，前端传的 ppu/amount 一律忽略；
      - 订单里的 ppu 是快照：商品以后调价，历史订单金额不变。
    """
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise OrderError('BAD_QUANTITY', '数量必须是 1~999 的整数') from None
    if not 1 <= quantity <= MAX_QUANTITY:
        raise OrderError('BAD_QUANTITY', f'数量必须在 1~{MAX_QUANTITY} 之间')

    with db.engine.begin() as conn:
        product = conn.execute(text(
            'SELECT product_id, ppu, product_status FROM products '
            'WHERE product_id = :pid'), {'pid': product_id}).mappings().first()
        if product is None:
            raise OrderError('PRODUCT_NOT_FOUND', '商品不存在')
        if product['product_status'] != 1:
            raise OrderError('PRODUCT_OFF_SHELF', '商品已下架，无法购买')

        ppu = Decimal(product['ppu'])
        amount = (ppu * quantity).quantize(Decimal('0.01'))
        transaction_id = ('W' + datetime.now().strftime('%Y%m%d%H%M%S')
                          + uuid.uuid4().hex[:6].upper())
        result = conn.execute(text(
            'INSERT INTO orders (transaction_id, order_date, customer_id, product_id, '
            'quantity, ppu, amount, order_status) '
            'VALUES (:tid, CURDATE(), :cid, :pid, :qty, :ppu, :amount, 1)'),
            {'tid': transaction_id, 'cid': customer_id, 'pid': product_id,
             'qty': quantity, 'ppu': ppu, 'amount': amount})

    logger.info('下单成功 order_id=%s transaction_id=%s customer=%s',
                result.lastrowid, transaction_id, customer_id)
    return {'order_id': result.lastrowid, 'transaction_id': transaction_id,
            'amount': amount}


def _load_order_for_update(conn, order_id):
    """订单行锁。和 Redis 锁是两层保险：
    Redis 锁挡住"同一订单并发"；FOR UPDATE 兜住"Redis 掉线/锁过期"的极端情况。"""
    return conn.execute(text(
        'SELECT order_id, customer_id, quantity, ppu, amount, order_status '
        'FROM orders WHERE order_id = :oid FOR UPDATE'), {'oid': order_id}
    ).mappings().first()


def _check_access(order, customer_id, as_staff):
    """归属校验：客户只能碰自己的订单；员工/管理员不限。

    不属于自己的订单返回 NOT_FOUND 而不是 FORBIDDEN：
    避免攻击者用"403/404 的差异"枚举订单 ID。
    """
    if not as_staff and order['customer_id'] != customer_id:
        raise OrderError('ORDER_NOT_FOUND', '订单不存在')


def update_quantity(order_id, quantity, customer_id=None, as_staff=False):
    """修改未支付订单的数量。金额用订单里的 ppu 快照重算，不用商品当前价。"""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise OrderError('BAD_QUANTITY', '数量必须是 1~999 的整数') from None
    if not 1 <= quantity <= MAX_QUANTITY:
        raise OrderError('BAD_QUANTITY', f'数量必须在 1~{MAX_QUANTITY} 之间')

    def _do():
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _load_order_for_update(conn, order_id)
                if order is None:
                    raise OrderError('ORDER_NOT_FOUND', '订单不存在')
                _check_access(order, customer_id, as_staff)
                if order['order_status'] == ORDER_PAID:
                    raise OrderError('ALREADY_PAID', '订单已支付，不能修改')
                if order['order_status'] != ORDER_UNPAID:
                    raise OrderError('ORDER_CLOSED', '订单已取消，不能修改')

                amount = (Decimal(order['ppu']) * quantity).quantize(Decimal('0.01'))
                result = conn.execute(text(
                    'UPDATE orders SET quantity = :qty, amount = :amount '
                    'WHERE order_id = :oid AND order_status = 1'),
                    {'qty': quantity, 'amount': amount, 'oid': order_id})
            if result.rowcount == 0:
                raise OrderError('STATE_CHANGED', '订单状态已变化，请刷新后重试')
            return {'order_id': order_id, 'quantity': quantity, 'amount': amount}

    return _run_locked(_do)


def cancel_order(order_id, customer_id=None, as_staff=False):
    """取消未支付订单（软删除：只把 order_status 改成 3，不删行）。"""
    def _do():
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _load_order_for_update(conn, order_id)
                if order is None:
                    raise OrderError('ORDER_NOT_FOUND', '订单不存在')
                _check_access(order, customer_id, as_staff)
                if order['order_status'] == ORDER_PAID:
                    raise OrderError('ALREADY_PAID', '订单已支付，请走退款流程')
                if order['order_status'] == ORDER_CANCELLED:
                    raise OrderError('ORDER_CLOSED', '订单已取消')

                result = conn.execute(text(
                    'UPDATE orders SET order_status = 3 '
                    'WHERE order_id = :oid AND order_status = 1'),
                    {'oid': order_id})
            if result.rowcount == 0:
                raise OrderError('STATE_CHANGED', '订单状态已变化，请刷新后重试')
            return {'order_id': order_id, 'cancelled': True}

    return _run_locked(_do)


def confirm_payment(order_id, staff_user_id):
    """员工确认线下收款：1 待付款 → 2 已支付。

    paid_at / paid_by 是审计字段：谁、什么时候确认的收款。
    这是 B 方案的"真实业务动作"——订单状态不再由假网关推进，而是由人确认。
    """
    def _do():
        with order_lock(order_id):
            with db.engine.begin() as conn:
                order = _load_order_for_update(conn, order_id)
                if order is None:
                    raise OrderError('ORDER_NOT_FOUND', '订单不存在')
                if order['order_status'] == ORDER_PAID:
                    raise OrderError('ALREADY_PAID', '订单已支付，请勿重复确认')
                if order['order_status'] != ORDER_UNPAID:
                    raise OrderError('ORDER_CLOSED', '订单已取消，无法确认收款')

                result = conn.execute(text(
                    'UPDATE orders SET order_status = 2, paid_at = NOW(), paid_by = :uid '
                    'WHERE order_id = :oid AND order_status = 1'),
                    {'uid': staff_user_id, 'oid': order_id})
            if result.rowcount == 0:
                raise OrderError('STATE_CHANGED', '订单状态已变化，请刷新后重试')
            return {'order_id': order_id, 'paid': True}

    return _run_locked(_do)