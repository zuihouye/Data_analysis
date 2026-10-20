import logging
import re

import click
from sqlalchemy.sql import text
from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from config import Config
from utils.rfm_report import MMK_PER_CNY, MMK_PER_USD


def _filter_cny(value):
    """金额筛选项：MMK 原值 -> 人民币元文本。

    数据库里所有金额（商品单价、订单快照价、RFM 的 M）都按数据集原始币种 MMK 存，
    页面统一按固定汇率折算成人民币展示；写入侧不动，避免把汇率写死进业务数据。
    """
    try:
        return '{:,.2f}'.format(float(value) / MMK_PER_CNY)
    except (TypeError, ValueError):
        return '—'


def _filter_usd(value):
    """金额筛选项：MMK 原值 -> 美元文本（KPI 的等价展示用）。"""
    try:
        return '{:,.2f}'.format(float(value) / MMK_PER_USD)
    except (TypeError, ValueError):
        return '—'


def create_app(config_object=Config):

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    # SECRET_KEY / SESSION_COOKIE_* / CSRF_ENABLED 都是从这里进 app.config 的
    # 漏了这一行 -> session 不可用，所有写操作和 {{ csrf_token() }} 都会炸
    app.config.from_object(config_object)

    # JSON 响应里的中文直接输出，不再转成 \uXXXX（方便在浏览器里看接口的错误信息）
    app.json.ensure_ascii = False

    # 模板里的金额过滤器：{{ order.amount|cny }} / {{ value|usd }}
    app.add_template_filter(_filter_cny, 'cny')
    app.add_template_filter(_filter_usd, 'usd')

    # 汇率也给模板一份：表单页要在 JS 里实时预览「折合人民币」，不想在模板里写死数字
    @app.context_processor
    def _inject_currency_rates():
        return {'MMK_PER_CNY': MMK_PER_CNY, 'MMK_PER_USD': MMK_PER_USD}

    from . import auth,clear_input,ge_RFM,order,user,pay
    app.register_blueprint(pay.pay_blue)
    app.register_blueprint(auth.auth_blue)
    app.register_blueprint(clear_input.ci_blue)
    app.register_blueprint(ge_RFM.ge_RFM_blue)
    app.register_blueprint(order.order_blue)
    app.register_blueprint(user.user_blue)

    _register_root(app)
    _register_error_handlers(app)
    _register_cli(app)
    return app


def _register_root(app):
    """根路径分流：客户进商品页，员工进客户管理页（未登录会被 @login_required 送到登录页）。

    注册在 create_app 里而不是 app.py 里：这样测试用 create_app() 造出来的
    app 实例也有 '/'，模板里的 url_for('hello_world') 任何实例都建得出来。
    """

    @app.route('/')
    def hello_world():
        if session.get('role') == 0:
            return redirect(url_for('order.product_list'))
        return redirect(url_for('user.customer_list'))

# 错误页文案：中文兜底，403 可用 abort(403, '...') 覆盖
_ERROR_PAGES = {
    403: ('没有访问权限', '当前账号无权访问这个页面。'),
    404: ('页面不存在', '链接可能已失效，或者内容已被删除。'),
    405: ('请求方式不被允许', '这个地址不接受当前的请求方式（例如直接 GET 了只接受 POST 的接口）。'),
    500: ('服务器开小差了', '处理请求时出现异常，本次操作没有完成，请稍后重试。'),
}
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def _register_error_handlers(app):
    """403 / 404 / 405 / 500 统一渲染 error_t.html（独立页，不依赖 base.html）."""

    def _render(code, exc=None):
        title, fallback = _ERROR_PAGES.get(code, ('出错了', '请求未能完成。'))
        message = fallback
        # abort(403, '需要管理员权限。') 这类中文描述优先展示；
        # werkzeug 默认描述是英文，直接用中文兜底文案，避免把英文原文丢给用户。
        if isinstance(exc, HTTPException) and exc.description and _CJK_RE.search(exc.description):
            message = exc.description
        return render_template('error_t.html', code=code, title=title,
                               message=message, path=request.path,
                               method=request.method), code

    @app.errorhandler(403)
    def _forbidden(exc):
        return _render(403, exc)

    @app.errorhandler(404)
    def _not_found(exc):
        return _render(404, exc)

    @app.errorhandler(405)
    def _method_not_allowed(exc):
        return _render(405, exc)

    @app.errorhandler(500)
    def _server_error(exc):
        original = getattr(exc, 'original_exception', None)
        app.logger.error('未处理异常: %s %s -> %r', request.method, request.path, original)
        return _render(500, exc)


def _register_cli(app):
    """flask --app app create-admin"""

    @app.cli.command('create_admin')
    @click.option('--phone', prompt='手机号(登录用)')
    @click.option('--username',prompt='显示名称')
    @click.option('--password', prompt='密码', hide_input=True, confirmation_prompt=True)
    @click.option('--role',type = click.Choice(['1','2']),default='2',show_default=True,help = '1=普通成员 2=管理员')

    def create_admin(phone,username,password,role):
        """创建或重置一个后台账号（默认管理员）。"""
        from utils import db
        role = int(role)
        password_hash = generate_password_hash(password)
        # 必须用 begin()：connect() 不会自动提交，CLI 命令退出后事务就回滚了
        with db.engine.begin() as conn:
            exists = conn.execute(
                text('select id from users where phone = :phone'),{'phone':phone}
            )
            if exists:
                conn.execute(
                    text('UPDATE users SET username = :username, password_hash = :password_hash, '
                         'role = :role, is_active = 1 WHERE phone = :phone'),
                    {'username': username, 'password_hash': password_hash,
                     'role': role, 'phone': phone}
                )
                click.echo(f'已更新账号：{phone} / {username}（角色 {role}）')
            else:
                conn.execute(
                    text('INSERT INTO users (phone, username, password_hash, role) '
                         'VALUES (:phone, :username, :password_hash, :role)'),
                    {'phone': phone, 'username': username,
                     'password_hash': password_hash, 'role': role})
                click.echo(f'已创建账号：{phone} / {username}（角色 {role}）')



