"""客户管理路由。

语义：客户是 CSV 导入的主数据 + 注册客户的混合体，
本文件只负责"员工看/改客户"，以及"管理员软注销客户"。
"""
import pandas as pd
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import text

from utils import db

from .auth import admin_required, staff_required

user_blue = Blueprint('user', __name__)




@user_blue.route('/customer/list')
@staff_required
def customer_list():
    """客户列表：员工/管理员可看；客户登录后访问这里返回 403。

    用 LEFT JOIN users 带出账号信息：
      - 老数据（CSV 导入的客户）没有账号 -> phone 为空、is_active 为 0；
      - 注册客户有账号 -> 显示手机号与启用状态。
    """
    sql = '''
        SELECT c.customer_id, c.region, c.cus_status,
               u.phone, u.is_active
        FROM customers c
        LEFT JOIN users u ON u.customer_id = c.customer_id
        ORDER BY c.customer_id
    '''
    df = pd.read_sql(sql, con=db.engine)
    df['phone'] = df['phone'].fillna('')
    df['is_active'] = df['is_active'].fillna(0).astype(int)  # NaN 在模板里不好判断
    datalist = df.to_dict(orient='records')
    return render_template('customer_list_t.html', datalist=datalist,
                           is_admin=session.get('role') == 2)



@user_blue.route('/customer/update', methods=['GET', 'POST'])
@staff_required
def customer_update():
    """修改客户地区（先从最小可用字段做起）。

    为什么只改地区：客户主数据的其他字段（ID/状态）要么不可变，
    要么有专门入口（注销）；地区是唯一"运营需要修正"的字段。
    """
    customer_id = (request.values.get('customer_id') or '').strip()
    with db.engine.connect() as conn:
        customer = conn.execute(text(
            'SELECT c.customer_id, c.region, c.cus_status, u.phone '
            'FROM customers c '
            'LEFT JOIN users u ON u.customer_id = c.customer_id '
            'WHERE c.customer_id = :cid'),
            {'cid': customer_id}).mappings().first()
    if customer is None:
        flash('客户不存在', 'danger')
        return redirect(url_for('user.customer_list'))

    if request.method == 'POST':
        region = (request.form.get('region') or '').strip()
        if not region or len(region) > 64:
            flash('地区不能为空且不超过 64 字', 'danger')
            return render_template('customer_form_t.html', customer=customer), 400
        with db.engine.begin() as conn:
            conn.execute(text(
                'UPDATE customers SET region = :region '
                'WHERE customer_id = :cid'),
                {'region': region, 'cid': customer_id})
        flash('客户地区已更新', 'success')
        return redirect(url_for('user.customer_list'))

    return render_template('customer_form_t.html', customer=customer)



@user_blue.route('/customer/delete', methods=['POST'])
@admin_required
def customer_delete():
    """注销客户 = 软删除：cus_status=2 + 账号 is_active=2。

    为什么不能 DELETE：
      orders.customer_id 有外键，物理删除会报 1451；
      而且订单是财务凭证，客户注销后历史订单必须还能查到。
    为什么同时停用账号：
      注销后还能登录、还能下单就自相矛盾了；停用后登录会提示"账号已停用"。
    """
    customer_id = (request.form.get('customer_id') or '').strip()
    with db.engine.begin() as conn:
        result = conn.execute(text(
            'UPDATE customers SET cus_status = 2 '
            'WHERE customer_id = :cid AND cus_status = 1'),
            {'cid': customer_id})
        if result.rowcount:
            conn.execute(text(
                'UPDATE users SET is_active = 2 '
                'WHERE customer_id = :cid AND is_active = 1'),
                {'cid': customer_id})

    if result.rowcount == 0:
        flash('客户不存在或已注销', 'warning')
    else:
        flash('客户已注销（订单保留；需要时可在列表中「恢复」）', 'success')
    return redirect(url_for('user.customer_list'))


@user_blue.route('/customer/restore', methods=['POST'])
@admin_required
def customer_restore():
    """恢复客户 = 反向软删除：cus_status 2→1，同时把被停用的账号重新启用。

    为什么要有这个入口：注销是软删除，状态存在库里，运维一定会遇到"注销错了"。
    只处理已注销的客户（cus_status=2），重复点恢复不会误改正常客户；
    账号只在 is_active=2（停用）时改回 1，无账号/本来就启用的客户不受影响。
    """
    customer_id = (request.form.get('customer_id') or '').strip()
    with db.engine.begin() as conn:
        result = conn.execute(text(
            'UPDATE customers SET cus_status = 1 '
            'WHERE customer_id = :cid AND cus_status = 2'),
            {'cid': customer_id})
        if result.rowcount:
            conn.execute(text(
                'UPDATE users SET is_active = 1 '
                'WHERE customer_id = :cid AND is_active = 2'),
                {'cid': customer_id})

    if result.rowcount == 0:
        flash('客户不存在，或不是已注销状态', 'warning')
    else:
        flash('客户已恢复，绑定的登录账号重新启用', 'success')
    return redirect(url_for('user.customer_list'))