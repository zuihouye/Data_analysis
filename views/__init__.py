import logging

import click
from sqlalchemy.sql import text
from flask import Flask
from werkzeug.security import generate_password_hash

from config import Config


def create_app(config_object=Config):

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    # SECRET_KEY / SESSION_COOKIE_* / CSRF_ENABLED 都是从这里进 app.config 的
    # 漏了这一行 -> session 不可用，所有写操作和 {{ csrf_token() }} 都会炸
    app.config.from_object(config_object)

    from . import auth,clear_input,ge_RFM,order,user
    app.register_blueprint(auth.auth_blue)
    app.register_blueprint(clear_input.ci_blue)
    app.register_blueprint(ge_RFM.ge_RFM_blue)
    app.register_blueprint(order.order_blue)
    app.register_blueprint(user.user_blue)

    _register_cli(app)
    return app

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



