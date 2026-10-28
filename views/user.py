"""客户管理路由。

语义：客户是 CSV 导入的主数据 + 注册客户的混合体，
本文件只负责"员工看/改客户"，以及"管理员软注销客户"。
"""
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import text

from utils import db
from utils.pagination import (SIZES, like_pattern, paginate, parse_choice,
                              parse_page, parse_q, parse_size)

from .auth import admin_required, staff_required

user_blue = Blueprint('user', __name__)

# ---------------------------------------------------------------- 列表筛选
# 白名单：URL 上的 status / account / sort 只能取这里的值，非法值回落默认。
CUSTOMER_STATUS_OPTIONS = (('', '全部状态'), ('1', '正常'), ('2', '已注销'))
CUSTOMER_ACCOUNT_OPTIONS = (('', '全部账号'), ('1', '已注册'), ('0', '未注册'))
# 客户 ID 是 Customer-1、Customer-10 这种字符串，纯字符串排序会排成 1、10、2；
# 用 SUBSTRING_INDEX 取最后一段数字转成整数，才是「自然序」。
CUSTOMER_NAT = "CAST(SUBSTRING_INDEX(c.customer_id, '-', -1) AS UNSIGNED)"
CUSTOMER_SORTS = {
    'id_asc': ('客户 ID 升序', CUSTOMER_NAT + ' ASC, c.customer_id ASC'),
    'id_desc': ('客户 ID 降序', CUSTOMER_NAT + ' DESC, c.customer_id DESC'),
    'region': ('按地区', 'c.region ASC, ' + CUSTOMER_NAT + ' ASC, c.customer_id ASC'),
}
CUSTOMER_FROM = (' FROM customers c '
                 'LEFT JOIN users u ON u.customer_id = c.customer_id')


def _page_args(source):
    """页码 + 每页条数（非法值由 parse_* 回落）。"""
    return {'page': parse_page(source.get('page')),
            'size': parse_size(source.get('size'))}


def _customer_filters(source):
    """解析并校验客户列表的筛选参数。"""
    return {
        'q': parse_q(source.get('q')),
        'status': parse_choice(source.get('status'), ('1', '2'), ''),
        'account': parse_choice(source.get('account'), ('1', '0'), ''),
        'sort': parse_choice(source.get('sort'), tuple(CUSTOMER_SORTS), 'id_asc'),
    }


def customer_list_args(source):
    """客户列表动作（改地区/注销/恢复）后 redirect 回原视图用的参数。"""
    args = _page_args(source)
    for key, value in _customer_filters(source).items():
        if value and not (key == 'sort' and value == 'id_asc'):
            args[key] = value
    return args


@user_blue.route('/customer/list')
@staff_required
def customer_list():
    """客户列表：员工/管理员可看；客户登录后访问这里返回 403。

    用 LEFT JOIN users 带出账号信息：
      - 老数据（CSV 导入的客户）没有账号 -> phone 为空、is_active 为 0；
      - 注册客户有账号 -> 显示手机号与启用状态。
    后端分页 + 关键词/状态/账号筛选 + 排序；客户 ID 默认按自然序。
    """
    filters = _customer_filters(request.args)

    conds, params = [], {}
    if filters['status']:
        conds.append('c.cus_status = :status')
        params['status'] = int(filters['status'])
    if filters['account'] == '1':
        conds.append('u.customer_id IS NOT NULL')
    elif filters['account'] == '0':
        conds.append('u.customer_id IS NULL')
    if filters['q']:
        conds.append('(c.customer_id LIKE :kw OR c.region LIKE :kw '
                     'OR u.phone LIKE :kw)')
        params['kw'] = like_pattern(filters['q'])
    where = (' WHERE ' + ' AND '.join(conds)) if conds else ''

    count_sql = 'SELECT COUNT(*)' + CUSTOMER_FROM + where
    data_sql = ('SELECT c.customer_id, c.region, c.cus_status, '
                'u.phone, u.is_active'
                + CUSTOMER_FROM + where
                + ' ORDER BY ' + CUSTOMER_SORTS[filters['sort']][1]
                + ' LIMIT :limit OFFSET :offset')
    # 「已注册」统计跟随当前筛选：在筛选结果里数有账号的
    registered_sql = ('SELECT COUNT(u.customer_id)' + CUSTOMER_FROM + where
                      + (' AND ' if where else ' WHERE ')
                      + 'u.customer_id IS NOT NULL')

    with db.engine.connect() as conn:
        rows, paging = paginate(conn, count_sql, data_sql, params,
                                parse_page(request.args.get('page')),
                                parse_size(request.args.get('size')))
        registered = conn.execute(text(registered_sql), params).scalar()

    items = []
    for row in rows:
        item = dict(row)
        item['phone'] = item['phone'] or ''
        # 无账号时 is_active 是 NULL，模板里判断成 0（无账号），别让 NaN 漏出来
        item['is_active'] = int(item['is_active']) if item['is_active'] else 0
        items.append(item)

    qs = {k: v for k, v in filters.items()
          if v and not (k == 'sort' and v == 'id_asc')}
    keep = {'page': paging['page'], 'size': paging['size'], **qs}

    return render_template('customer_list_t.html', items=items,
                           paging=paging, sizes=SIZES,
                           registered=int(registered or 0),
                           is_admin=session.get('role') == 2,
                           filters=filters, qs=qs, keep=keep,
                           status_options=CUSTOMER_STATUS_OPTIONS,
                           account_options=CUSTOMER_ACCOUNT_OPTIONS,
                           sort_options=[(k, v[0]) for k, v in CUSTOMER_SORTS.items()])


@user_blue.route('/customer/update', methods=['GET', 'POST'])
@staff_required
def customer_update():
    """修改客户地区（先从最小可用字段做起）。

    为什么只改地区：客户主数据的其他字段（ID/状态）要么不可变，
    要么有专门入口（注销）；地区是唯一"运营需要修正"的字段。
    """
    customer_id = (request.values.get('customer_id') or '').strip()
    ret = customer_list_args(request.values)
    with db.engine.connect() as conn:
        customer = conn.execute(text(
            'SELECT c.customer_id, c.region, c.cus_status, u.phone '
            'FROM customers c '
            'LEFT JOIN users u ON u.customer_id = c.customer_id '
            'WHERE c.customer_id = :cid'),
            {'cid': customer_id}).mappings().first()
    if customer is None:
        flash('客户不存在', 'danger')
        return redirect(url_for('user.customer_list', **ret))

    if request.method == 'POST':
        region = (request.form.get('region') or '').strip()
        if not region or len(region) > 64:
            flash('地区不能为空且不超过 64 字', 'danger')
            return render_template('customer_form_t.html', customer=customer,
                                   ret=ret), 400
        with db.engine.begin() as conn:
            conn.execute(text(
                'UPDATE customers SET region = :region '
                'WHERE customer_id = :cid'),
                {'region': region, 'cid': customer_id})
        flash('客户地区已更新', 'success')
        return redirect(url_for('user.customer_list', **ret))

    return render_template('customer_form_t.html', customer=customer, ret=ret)



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
    return redirect(url_for('user.customer_list', **customer_list_args(request.form)))


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
    return redirect(url_for('user.customer_list', **customer_list_args(request.form)))