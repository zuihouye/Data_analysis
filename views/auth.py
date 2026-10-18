"""登录、会话与权限控制。

认证、CSRF、权限是同一件事的三个面，放在一个文件里，避免"漏挂一个视图就漏一个洞"。

设计要点（面试可讲）：
1. 角色由服务端从 users.role 读出，绝不接受客户端提交；
2. 密码只存 werkzeug 哈希，校验用 check_password_hash（自带盐与耗时参数）；
3. CSRF 统一在 before_app_request 里校验，而不是每个视图手动调；
4. 登录失败不区分"用户不存在"与"密码错"，避免账号枚举；
5. 登录成功后先 session.clear()，防会话固定攻击。
"""

import logging
import secrets
import hmac
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, session, request, current_app, redirect, url_for,render_template
from sqlalchemy import text
from werkzeug.security import check_password_hash

from utils import db

logger = logging.getLogger(__name__)

auth_blue = Blueprint("auth", __name__)

ROLE_NAMES = {1: '普通成员', 2: '管理员'}

# ------------------------------------------------------------------ 会话
def current_user():
    """从 session 还原当前登录用户；未登录返回 None。模板里当 current_user 用。"""
    user_id = session.get("user_id")
    if not user_id:
        return None
    role = session.get("role",1)
    return {
        'id': user_id,
        'username': session.get("username",''),
        'phone': session.get("phone",''),
        'role': role,
        'role_name': ROLE_NAMES.get(role, '未知'),
    }

def _save_login(row):
    """写入登录态。
    先 clear 再写：换一个新的 session，防会话固定攻击（攻击者事先塞给受害者一个
    已知的 session id，受害者用它登录后，攻击者就能复用这个 id 冒充他）。

    代价：CSRF token 跟着一起轮换，登录之前渲染出来的旧表单会失效。
    浏览器登录后必然重新渲染页面、拿到新 token，所以正常流程无感 ——
    这一点实测过（见本文档第 13 节"验证记录"）。
    """
    session.clear()
    session["user_id"] = row['id']
    session["username"] = row['username']
    session["phone"] = row['phone']
    session["role"] = row['role']
    session["_csrf_token"] = secrets.token_urlsafe(32)  # 显式轮换，由下一个页面渲染给浏览器
    session.permanent = True

# ------------------------------------------------------------------ CSRF
def generate_csrf_token():
    """取当前会话的 CSRF token，没有就生成。模板里写 {{ csrf_token() }}。"""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token

def _csrf_valid():
    sent = (request.form.get("csrf_token")
            or request.headers.get("X-CSRF-Token")
            or '')
    real = session.get("_csrf_token") or ''
    # 常量时间比较：普通 == 会在第一个不同字符处返回，理论上可被计时侧信道逐字节猜
    return bool(real) and hmac.compare_digest(sent, real)

@auth_blue.before_app_request
def csrf_protect():
     """所有写方法（POST/PUT/PATCH/DELETE）统一校验 CSRF，视图里不用再写。"""
     if request.method not in ["POST", "PUT", "PATCH", "DELETE"]:
         return
     if not current_app.config.get("CSRF_ENABLED", True):
         return
     if not _csrf_valid():
         logging.warning('CSRF 校验失败: %s %s', request.method, request.path)
         return 'CSRF 校验失败：请刷新页面后重试。', 400



# ------------------------------------------------------------- 权限装饰器

def login_required(view):
    @wraps(view)        # 不写 wraps 会丢掉 __name__，多个视图都叫 wrapper 时 Flask 会报端点名冲突
    def warpper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for('auth.login',next = _current_target()))
        return view(*args, **kwargs)
    return warpper

def admin_required(view):
    """管理员专用。内部已含未登录判断，不需要再叠一层 @login_required。"""
    @wraps(view)
    def warpper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for('auth.login',next = _current_target()))
        if session.get("role") != 2:
            logger.warning('越权访问被拦截: user=%s %s',
                           session.get('user_id'), request.path)
            return '需要管理员权限。', 403
        return view(*args, **kwargs)
    return warpper

# ------------------------------------------------------------- 登录 / 登出
def _current_target():
    """当前请求的站内路径（带查询串），登录后跳回来用。"""
    query = request.query_string.decode()
    return f'{request.path}?{query}' if query else request.path

def _safe_next():
    """只接受站内绝对路径，挡住开放重定向（?next=https://evil.com）。"""
    target = request.values.get('next')
    if not target or not target.startswith('/') or target.startswith('//'):
        return None
    parts = urlsplit(target)
    if parts.netloc or parts.scheme:
        return None
    return target

@auth_blue.route('/login', methods=['GET', 'POST'])
def login():
    if session.get("user_id"):
        return redirect(_safe_next() or url_for("user.customer_list"))

    if request.method == "POST":
        # 登录页的表单名是 mobile / pwd，和 users 表的 phone / password_hash 不同名，这里做映射
        phone = (request.form.get("mobile") or '').strip()
        password = request.form.get("pwd") or ''

        row = None
        if phone and password:
            with db.engine.connect() as conn:
                row = conn.execute(text(
                    'SELECT id, phone, username, password_hash, role, is_active '
                    'FROM users WHERE phone = :phone'),{'phone': phone}).mappings().first()

        if row is None or not check_password_hash(row['password_hash'], password):
            logging.info('登录失败: phone=%s', phone)
            # 统一提示，不暴露"这个手机号是否存在"
            return render_template('login_t.html', error='手机号或密码错误'),401

        if row['is_active'] !=1:
            return render_template('login_t.html',error ='账号已停用，请联系管理员'),403

        _save_login(row)
        logger.info('登录成功: user_id=%s role=%s', row['id'], row['role'])
        return redirect(_safe_next() or url_for("user.customer_list"))

    return render_template('login_t.html')

@auth_blue.route('/logout', methods=['POST'])
def logout():
    """用 POST：登出改变服务端状态，GET 版可被 <img src="/logout"> 跨站触发。"""
    user_id = session.get("user_id")
    session.clear()
    logger.info('已登出: user_id=%s', user_id)
    return redirect(url_for("auth.login"))

# ------------------------------------------------------------- 模板注入
@auth_blue.app_context_processor
def inject_auth():
    """让所有模板都能用 current_user 和 csrf_token()。"""
    return {'current_user': current_user(), 'csrf_token': generate_csrf_token}




