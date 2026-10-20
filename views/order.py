"""订单与商品路由。

分层：本文件只做"解析请求 + 权限 + 渲染/flash 提示"，
业务规则和并发控制在 utils/order_ops.py，两者可以独立测试。

安全约定（每一行都有原因）：
  1. 查询/操作订单一律挂 @login_required / @staff_required / @customer_required；
  2. 客户看到的订单 SQL 带 WHERE customer_id = session 里的值（数据隔离在 SQL 层做）；
  3. 客户下单时 customer_id 只从 session 取，绝不读表单；
  4. 破坏性操作全部 POST + CSRF（CSRF 由 auth.py 的 before_app_request 统一校验）。
"""
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import text

from utils import db
from utils.order_ops import (OrderError, cancel_order, create_order, get_order,
                             update_quantity)

from .auth import customer_required, login_required, staff_required

order_blue = Blueprint('order', __name__)

# 状态文案放一处，模板里直接用 status_text[data.order_status]
STATUS_TEXT = {1: '待付款', 2: '已支付', 3: '已取消'}


def _parse_product_form():
    """校验商品表单。返回 (data, error)，error 是给用户看的文案。"""
    name = (request.form.get('product_name') or '').strip()
    category = (request.form.get('product_category') or '').strip()
    ppu_raw = (request.form.get('ppu') or '').strip()

    if not name or len(name) > 128:
        return None, '商品名称不能为空且不超过 128 字'
    if len(category) > 64:
        return None, '商品类别不超过 64 字'
    try:
        ppu = Decimal(ppu_raw).quantize(Decimal('0.01'))
        if ppu <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return None, '单价必须是大于 0 的数字'
    return {'product_name': name,
            'product_category': category or None,
            'ppu': ppu}, None

# ---------------------------------------------------------------- 列表页

@order_blue.route('/order/list')
@login_required
def order_list():
    """订单列表：客户只看自己的，员工/管理员看全部。

    注意查了 o.order_id —— 原来的 SQL 漏了它，模板只能拿 transaction_id 冒充，
    导致"删除订单"传错参数。这是本轮要一起修的 bug。
    """
    is_staff = session.get('role') in (1, 2)
    sql = '''
        SELECT o.order_id, o.transaction_id, o.order_date, o.customer_id,
               p.product_name, o.quantity, o.ppu, o.amount,
               o.order_status, o.paid_at
        FROM orders o
        LEFT JOIN products p ON o.product_id = p.product_id
    '''
    params = {}
    if not is_staff:
        sql += ' WHERE o.customer_id = :customer_id'
        params['customer_id'] = session.get('customer_id')
    sql += ' ORDER BY o.order_date DESC, o.order_id DESC'

    df = pd.read_sql(text(sql), con=db.engine, params=params)
    df['order_date'] = df['order_date'].astype(str)
    # NaT 是 pandas 的空日期；转成空串，模板不用再判断
    df['paid_at'] = df['paid_at'].astype(str).replace({'NaT': '', 'None': ''})
    return render_template('order_list_t.html',
                           datalist=df.to_dict(orient='records'),
                           is_staff=is_staff,
                           status_text=STATUS_TEXT)

@order_blue.route('/product/list')
@login_required
def product_list():
    """商品列表：客户只看在售（product_status=1），员工看全部。"""
    is_staff = session.get('role') in (1, 2)
    sql = ('SELECT product_id, product_name, product_category, ppu, product_status '
           'FROM products')
    if not is_staff:
        sql += ' WHERE product_status = 1'
    sql += ' ORDER BY product_id'

    df = pd.read_sql(sql, con=db.engine)
    return render_template('product_list_t.html',
                           datalist=df.to_dict(orient='records'),
                           is_staff=is_staff)


# ---------------------------------------------------------------- 商品维护

@order_blue.route('/product/add', methods=['GET', 'POST'])
@staff_required
def product_add():
    """新增商品。

    权限：你要求"员工也能改商品"，所以挂 @staff_required（role 1/2 都行）。
    想只让管理员操作，把这一行换成 @admin_required 即可，其余代码不用动。
    """
    if request.method == 'POST':
        data, error = _parse_product_form()
        if error:
            flash(error, 'danger')
            return render_template('product_form_t.html', mode='add',
                                   form=request.form), 400

        product_id = ('SKU-' + datetime.now().strftime('%Y%m%d%H%M%S')
                      + uuid.uuid4().hex[:4].upper())
        with db.engine.begin() as conn:
            conn.execute(text(
                'INSERT INTO products (product_id, product_name, product_category, '
                'ppu, product_status) '
                'VALUES (:pid, :name, :cat, :ppu, 1)'),
                {'pid': product_id, 'name': data['product_name'],
                 'cat': data['product_category'], 'ppu': data['ppu']})
        flash(f'商品已添加：{data["product_name"]}（{product_id}）', 'success')
        return redirect(url_for('order.product_list'))

    return render_template('product_form_t.html', mode='add', form={})


@order_blue.route('/product/update', methods=['GET', 'POST'])
@staff_required
def product_update():
    """编辑商品：名称/类别/单价/上下架。"""
    product_id = (request.values.get('product_id') or '').strip()
    with db.engine.connect() as conn:
        product = conn.execute(text(
            'SELECT product_id, product_name, product_category, ppu, product_status '
            'FROM products WHERE product_id = :pid'),
            {'pid': product_id}).mappings().first()
    if product is None:
        flash('商品不存在', 'danger')
        return redirect(url_for('order.product_list'))

    if request.method == 'POST':
        data, error = _parse_product_form()
        status = request.form.get('product_status', '1')
        if error:
            flash(error, 'danger')
            return render_template('product_form_t.html', mode='edit',
                                   form=dict(product)), 400
        if status not in ('1', '2'):
            flash('商品状态非法', 'danger')
            return render_template('product_form_t.html', mode='edit',
                                   form=dict(product)), 400

        with db.engine.begin() as conn:
            conn.execute(text(
                'UPDATE products SET product_name = :name, '
                'product_category = :cat, ppu = :ppu, product_status = :status '
                'WHERE product_id = :pid'),
                {'name': data['product_name'], 'cat': data['product_category'],
                 'ppu': data['ppu'], 'status': int(status), 'pid': product_id})
        flash('商品已更新（历史订单金额不受调价影响）', 'success')
        return redirect(url_for('order.product_list'))

    return render_template('product_form_t.html', mode='edit', form=dict(product))


@order_blue.route('/product/delete', methods=['POST'])
@staff_required
def product_delete():
    """下架商品 = 软删除：product_status 改 2，商品行保留。

    为什么不能 DELETE：orders.product_id 有外键，物理删除会失败；
    而且历史订单还要靠它显示商品名（order_list 里 LEFT JOIN products）。
    """
    product_id = (request.form.get('product_id') or '').strip()
    with db.engine.begin() as conn:
        result = conn.execute(text(
            'UPDATE products SET product_status = 2 '
            'WHERE product_id = :pid AND product_status = 1'),
            {'pid': product_id})
    if result.rowcount == 0:
        flash('商品不存在或已下架', 'warning')
    else:
        flash('商品已下架，历史订单仍保留', 'success')
    return redirect(url_for('order.product_list'))


# ---------------------------------------------------------------- 客户下单

@order_blue.route('/order/create', methods=['GET', 'POST'])
@customer_required
def order_create():
    """客户下单：只允许 role=0，且 customer_id 来自 session。"""
    if request.method == 'POST':
        # 一次性表单 token：防"双击提交 / 刷新重发"生成两笔订单。
        # 页面每次 GET 都会生成新 token，成功提交一次后 session 里就没了。
        expected = session.pop('_order_form_token', None)
        if not expected or request.form.get('form_token') != expected:
            flash('页面已过期，请重新填写', 'warning')
            return redirect(url_for('order.order_create'))

        try:
            result = create_order(customer_id=session['customer_id'],
                                  product_id=(request.form.get('product_id') or '').strip(),
                                  quantity=request.form.get('quantity'))
        except OrderError as exc:
            flash(exc.msg, 'danger')
            return redirect(url_for('order.order_create'))

        flash(f'下单成功：{result["transaction_id"]}，'
              f'应付 ¥{result["amount"]:.2f}（等待员工确认收款）', 'success')
        return redirect(url_for('order.order_list'))

    # GET：生成新 token + 列出在售商品
    form_token = uuid.uuid4().hex
    session['_order_form_token'] = form_token
    df = pd.read_sql(
        'SELECT product_id, product_name, product_category, ppu FROM products '
        'WHERE product_status = 1 ORDER BY product_id', con=db.engine)
    return render_template('order_create_t.html',
                           products=df.to_dict(orient='records'),
                           preselect=request.args.get('product_id', ''),
                           form_token=form_token)


# ---------------------------------------------------------------- 改 / 取消

@order_blue.route('/order/update', methods=['GET', 'POST'])
@login_required
def order_update():
    """修改订单数量：客户只能改自己的，员工/管理员可改任意订单，且都必须是未支付。

    为什么只能改数量：金额=数量×快照单价，改数量还能保持订单自洽；
    允许换商品就等于重新取价下单，那是另一笔业务（直接取消重下更清晰）。
    """
    order_id = request.values.get('order_id', type=int)
    order = get_order(order_id) if order_id else None
    is_staff = session.get('role') in (1, 2)

    # 不存在 / 不是自己的订单：统一提示"不存在"，不暴露别人的订单 ID
    if order is None or (not is_staff and order['customer_id'] != session.get('customer_id')):
        flash('订单不存在', 'danger')
        return redirect(url_for('order.order_list'))
    if order['order_status'] != 1:
        flash('只有待付款订单可以修改', 'warning')
        return redirect(url_for('order.order_list'))

    if request.method == 'POST':
        try:
            update_quantity(order_id,
                            request.form.get('quantity'),
                            customer_id=session.get('customer_id'),
                            as_staff=is_staff)
        except OrderError as exc:
            flash(exc.msg, 'danger')
        else:
            flash('订单数量已更新', 'success')
        return redirect(url_for('order.order_list'))

    return render_template('order_form_t.html', order=order)


@order_blue.route('/order/delete', methods=['POST'])
@login_required
def order_delete():
    """取消订单（软删除：order_status 改 3，订单行保留）。

    和 /pay/cancel 调的是同一个 utils.order_ops.cancel_order，
    所以页面入口和 JSON 接口的并发语义、错误码完全一致。
    """
    order_id = request.form.get('order_id', type=int)
    if not order_id:
        flash('订单参数错误', 'danger')
        return redirect(url_for('order.order_list'))

    try:
        cancel_order(order_id,
                     customer_id=session.get('customer_id'),
                     as_staff=session.get('role') in (1, 2))
    except OrderError as exc:
        flash(exc.msg, 'danger')
    else:
        flash('订单已取消（订单记录保留）', 'success')
    return redirect(url_for('order.order_list'))