# P0-2：登录 / 权限 / CSRF 实施方案（含完整代码）

> 目标：把 `login_t.html` 这个"孤儿页面"接上路由与 session，让 `layout_t.html` 里
> 一直空白的 `{{ 用户名 }}` 有真实来源，并给 `/clear_input` 这类写操作补上
> **认证 + 授权 + CSRF** 三层防护。
>
> 对照 `docs/01-改造路线图.md` 第 4 章、2.1 节。**改动 8 个文件，全部代码在下面，逐个文件替换即可。**

---

## 0. 改动清单

| # | 文件 | 动作 |
|---|---|---|
| 1 | `.env` / `.env.example` | 新增 `SECRET_KEY` |
| 2 | `config.py` | 整体替换：加 `SECRET_KEY`、会话 cookie 参数、CSRF 开关 |
| 3 | `views/auth.py` | **新建**：登录 / 登出 / `login_required` / `admin_required` / CSRF |
| 4 | `views/__init__.py` | 整体替换：加载配置、注册 auth 蓝图、加 `create-admin` 命令 |
| 5 | `views/clear_input.py` | 整体替换：挂 `@admin_required` |
| 6 | `views/ge_RFM.py`、`order.py`、`user.py` | 各加 2 行：导入装饰器 + 挂 `@login_required` |
| 7 | `app.py` | 根路径从 `'Hello World!'` 改成跳客户列表 |
| 8 | `templates/login_t.html`、`layout_t.html` | 删掉"身份选择"、补 CSRF token、接上真实用户信息 + 退出登录 |
| 9 | `tests/test_import_db.py` | 2 条路由测试改造 + 新增 4 条安全测试 |

---

## 1. 五个设计决策（先看这段，决定要不要照抄）

1. **身份不接受客户端提交。** 登录页原来有个"普通成员 / 管理员"下拉框，等于把越权入口摆在登录页上。
   正确做法：登录只收手机号 + 密码，角色由服务端从 `users.role` 读出后写进 session。
2. **CSRF 校验放在钩子里，不放在视图里。** 用 `before_app_request` 对所有写方法统一校验；
   靠"记得给每个视图加一行"防漏洞，迟早会漏一个。
3. **登出用 POST，不用 `<a href="/logout">`。** 登出会改变服务端状态，GET 能被
   `<img src="/logout">` 跨站触发。
4. **登录失败提示不区分"用户不存在"和"密码错误"**，避免账号枚举。
5. **登录成功后 `session.clear()` 再写入**，防会话固定攻击（Session Fixation）。

> 与路线图的一处**有意偏差**：路线图第 4 章写的是 `views/user.py` 里的 `user.login`。
> 但 `views/user.py` 现在装的是**客户**路由（`/customer/list`），把认证塞进去会让这个文件同时管两件事。
> 这里改成**独立的 `views/auth.py`（蓝图名 `auth`）**，端点变成 `auth.login` / `auth.logout`。
> 如果你更想跟路线图严格一致，把所有 `url_for('auth.login')` 改成 `url_for('user.login')` 并把这些代码挪进 `user.py` 即可，逻辑不变。

---

## 2. 步骤 1：`.env` 加密钥

先生成一个随机密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**`.env`**（追加一行，把上一步的输出粘进去）：

```ini
SECRET_KEY=粘这里
```

**`.env.example`**（整体替换，模板里要留说明但不能留真密钥）：

```ini
DB_USER=root
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=3306
DB_NAME=taobao_user

# Flask session 签名密钥。生成方式：
#   python -c "import secrets; print(secrets.token_hex(32))"
# 不设置也能跑（会临时随机生成），但每次重启所有登录都会失效。
SECRET_KEY=
```

---

## 3. 步骤 2：`config.py`（整体替换）

```python
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# 显式指定路径，避免"cwd 不同就找不到 .env"的玄学问题
load_dotenv(Path(__file__).resolve().parent / '.env')


class Config:
    # ---------------------------------------------------------- 数据库
    DB_USER     = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_HOST     = os.getenv('DB_HOST', 'localhost')
    DB_PORT     = os.getenv('DB_PORT', '3306')
    DB_NAME     = os.getenv('DB_NAME', 'taobao_user')

    # ---------------------------------------------------------- 会话
    # 缺失时临时随机生成：方便本地起步，但重启即失效，所以只够开发用
    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)

    SESSION_COOKIE_HTTPONLY = True     # JS 读不到 cookie，XSS 偷不走 session
    SESSION_COOKIE_SAMESITE = 'Lax'    # 跨站请求不带 cookie，CSRF 的第一道防线
    SESSION_COOKIE_SECURE   = False    # 本地是 http；上线换 https 后必须改成 True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # 仅在测试里允许关掉；生产必须是 True
    CSRF_ENABLED = True

    @classmethod
    def db_uri(cls):
        if not cls.DB_PASSWORD:
            raise RuntimeError(
                "环境变量 DB_PASSWORD 未设置。"
                "请复制 .env.example 为 .env 并填写密码。"
            )
        return (f"mysql+pymysql://{cls.DB_USER}:{quote_plus(cls.DB_PASSWORD)}"
                f"@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}?charset=utf8mb4")


if not os.getenv('SECRET_KEY'):
    logging.getLogger(__name__).warning(
        '未设置 SECRET_KEY，已临时生成随机密钥：重启后所有登录会话失效。'
        '请在 .env 中设置，生成方式：'
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
```

---

## 4. 步骤 3：`views/auth.py`（新建，完整内容）

```python
"""登录、会话与权限控制。

认证、CSRF、权限是同一件事的三个面，放在一个文件里，避免"漏挂一个视图就漏一个洞"。

设计要点（面试可讲）：
1. 角色由服务端从 users.role 读出，绝不接受客户端提交；
2. 密码只存 werkzeug 哈希，校验用 check_password_hash（自带盐与耗时参数）；
3. CSRF 统一在 before_app_request 里校验，而不是每个视图手动调；
4. 登录失败不区分"用户不存在"与"密码错"，避免账号枚举；
5. 登录成功后先 session.clear()，防会话固定攻击。
"""
import hmac
import logging
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import (Blueprint, current_app, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import text
from werkzeug.security import check_password_hash

from utils import db

logger = logging.getLogger(__name__)

auth_blue = Blueprint('auth', __name__)

ROLE_NAMES = {1: '普通成员', 2: '管理员'}


# ------------------------------------------------------------------ 会话
def current_user():
    """从 session 还原当前登录用户；未登录返回 None。模板里当 current_user 用。"""
    user_id = session.get('user_id')
    if not user_id:
        return None
    role = session.get('role', 1)
    return {
        'id': user_id,
        'username': session.get('username', ''),
        'phone': session.get('phone', ''),
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
    session['user_id'] = row['id']
    session['username'] = row['username']
    session['phone'] = row['phone']
    session['role'] = row['role']
    session['_csrf_token'] = secrets.token_urlsafe(32)   # 显式轮换，由下一个页面渲染给浏览器
    session.permanent = True


# ------------------------------------------------------------------ CSRF
def generate_csrf_token():
    """取当前会话的 CSRF token，没有就生成。模板里写 {{ csrf_token() }}。"""
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def _csrf_valid():
    sent = (request.form.get('csrf_token')
            or request.headers.get('X-CSRF-Token')
            or '')
    real = session.get('_csrf_token') or ''
    # 常量时间比较：普通 == 会在第一个不同字符处返回，理论上可被计时侧信道逐字节猜
    return bool(real) and hmac.compare_digest(sent, real)


@auth_blue.before_app_request
def csrf_protect():
    """所有写方法（POST/PUT/PATCH/DELETE）统一校验 CSRF，视图里不用再写。"""
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return
    if not current_app.config.get('CSRF_ENABLED', True):
        return
    if not _csrf_valid():
        logger.warning('CSRF 校验失败: %s %s', request.method, request.path)
        return 'CSRF 校验失败：请刷新页面后重试。', 400


# ------------------------------------------------------------- 权限装饰器
def login_required(view):
    @wraps(view)   # 不写 wraps 会丢掉 __name__，多个视图都叫 wrapper 时 Flask 会报端点名冲突
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=_current_target()))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    """管理员专用。内部已含未登录判断，不需要再叠一层 @login_required。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=_current_target()))
        if session.get('role') != 2:
            logger.warning('越权访问被拦截: user=%s %s',
                           session.get('user_id'), request.path)
            return '需要管理员权限。', 403
        return view(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------- 登录 / 登出
def _current_target():
    """当前请求的站内路径（带查询串），登录后跳回来用。"""
    query = request.query_string.decode()
    return f'{request.path}?{query}' if query else request.path


def _safe_next():
    """只接受站内绝对路径，挡住开放重定向（?next=https://evil.com）。"""
    target = request.values.get('next') or ''
    if not target or not target.startswith('/') or target.startswith('//'):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


@auth_blue.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(_safe_next() or url_for('user.customer_list'))

    if request.method == 'POST':
        # 登录页的表单名是 mobile / pwd，和 users 表的 phone / password_hash 不同名，这里做映射
        phone = (request.form.get('mobile') or '').strip()
        password = request.form.get('pwd') or ''

        row = None
        if phone and password:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text('SELECT id, phone, username, password_hash, role, is_active '
                         'FROM users WHERE phone = :phone'),
                    {'phone': phone},
                ).mappings().first()

        if row is None or not check_password_hash(row['password_hash'], password):
            logger.info('登录失败: phone=%s', phone)
            # 统一提示，不暴露"这个手机号是否存在"
            return render_template('login_t.html', error='手机号或密码错误'), 401

        if row['is_active'] != 1:
            return render_template('login_t.html', error='账号已停用，请联系管理员'), 403

        _save_login(row)
        logger.info('登录成功: user_id=%s role=%s', row['id'], row['role'])
        return redirect(_safe_next() or url_for('user.customer_list'))

    return render_template('login_t.html')


@auth_blue.route('/logout', methods=['POST'])
def logout():
    """用 POST：登出改变服务端状态，GET 版可被 <img src="/logout"> 跨站触发。"""
    user = session.get('user_id')
    session.clear()
    logger.info('已登出: user_id=%s', user)
    return redirect(url_for('auth.login'))


# ------------------------------------------------------------- 模板注入
@auth_blue.app_context_processor
def inject_auth():
    """让所有模板都能用 current_user 和 csrf_token()。"""
    return {'current_user': current_user(), 'csrf_token': generate_csrf_token}
```

---

## 5. 步骤 4：`views/__init__.py`（整体替换）

```python
import logging

import click
from flask import Flask
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from config import Config


def create_app(config_object=Config):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    # SECRET_KEY / SESSION_COOKIE_* / CSRF_ENABLED 都是从这里进 app.config 的
    app.config.from_object(config_object)

    from . import auth, clear_input, ge_RFM, order, user
    app.register_blueprint(auth.auth_blue)
    app.register_blueprint(clear_input.ci_blue)
    app.register_blueprint(ge_RFM.ge_RFM_blue)
    app.register_blueprint(order.order_blue)
    app.register_blueprint(user.user_blue)

    _register_cli(app)
    return app


def _register_cli(app):
    """flask --app app create-admin"""

    @app.cli.command('create-admin')
    @click.option('--phone', prompt='手机号（登录用）')
    @click.option('--username', prompt='显示名')
    @click.option('--password', prompt='密码', hide_input=True, confirmation_prompt=True)
    @click.option('--role', type=click.Choice(['1', '2']), default='2', show_default=True,
                  help='1=普通成员 2=管理员')
    def create_admin(phone, username, password, role):
        """创建或重置一个后台账号（默认管理员）。"""
        from utils import db          # 延迟导入：CLI 命令注册阶段不碰数据库
        role = int(role)
        password_hash = generate_password_hash(password)
        with db.engine.begin() as conn:
            exists = conn.execute(
                text('SELECT id FROM users WHERE phone = :phone'),
                {'phone': phone}).scalar()
            if exists:
                conn.execute(
                    text('UPDATE users SET username = :username, password_hash = :password_hash, '
                         'role = :role, is_active = 1 WHERE phone = :phone'),
                    {'username': username, 'password_hash': password_hash,
                     'role': role, 'phone': phone})
                click.echo(f'已更新账号：{phone} / {username}（角色 {role}）')
            else:
                conn.execute(
                    text('INSERT INTO users (phone, username, password_hash, role) '
                         'VALUES (:phone, :username, :password_hash, :role)'),
                    {'phone': phone, 'username': username,
                     'password_hash': password_hash, 'role': role})
                click.echo(f'已创建账号：{phone} / {username}（角色 {role}）')
```

---

## 6. 步骤 5：`views/clear_input.py`（整体替换）

改动只有两处：导入 `admin_required`、给视图挂装饰器。其余保持原样。

```python
import logging

from flask import Blueprint, jsonify

from utils import loader
from utils.quality import DataQualityError

from .auth import admin_required

logger = logging.getLogger(__name__)

ci_blue = Blueprint('ci_blue', __name__)


# 安全层已补齐：
#   POST（不是 GET）+ CSRF（before_app_request 统一校验）+ 管理员权限
# 剩下的"前端二次确认"见路线图 2.1，属于体验优化，不是安全边界。
@ci_blue.route('/clear_input', methods=['POST'])
@admin_required
def clear_input():
    try:
        df, report = loader.process_and_import()
    except DataQualityError as exc:
        logger.warning('数据质量不达标，已中止导入: %s', exc)
        return jsonify({
            'status': 'error',
            'message': f'数据质量不达标，已中止导入：{exc}',
        }), 400

    # 日期转成字符串再序列化，否则 Flask 会输出 RFC 822 格式（Wed, 01 Jan 2025 ...）
    preview = df.head(20).copy()
    preview['order_date'] = preview['order_date'].dt.strftime('%Y-%m-%d')

    return jsonify({
        'status': 'success',
        'message': '数据加载成功！',
        'total_rows': len(df),
        'quality_report': report,
        'data_preview': preview.to_dict(orient='records'),
    })
```

---

## 7. 步骤 6：其余三个视图各加两行

**`views/ge_RFM.py`**

顶部导入区加一行：

```python
from .auth import login_required
```

然后给视图挂装饰器：

```python
@ge_RFM_blue.route('/ge_rfm')
@login_required
def ge_rfm():
    ...
```

**`views/order.py`**：同样加 `from .auth import login_required`，然后

```python
@order_blue.route('/order/list')
@login_required
def order_list():
    ...


@order_blue.route('/product/list')
@login_required
def product_list():
    ...
```

**`views/user.py`**：同样加 `from .auth import login_required`，然后

```python
@user_blue.route('/customer/list')
@login_required
def customer_list():
    ...
```

> 那 7 个空壳路由（`pass` / `return None`）**先别挂**。它们现在本来就是 500，
> 等 P1 用真实 CRUD 实现时再一起加 `@login_required`，免得挂了个要登录的 500。

---

## 8. 步骤 7：`app.py`（整体替换）

```python
from flask import redirect, url_for

from views import create_app

app = create_app()


@app.route('/')
def hello_world():
    # 根路径进客户列表；未登录会被 @login_required 送到登录页
    return redirect(url_for('user.customer_list'))


if __name__ == '__main__':
    app.run()
```

---

## 9. 步骤 8：模板改动（4 处）

### 9.1 `templates/login_t.html`：删掉"身份选择"下拉框

把这一段（大约第 46–53 行）**整段删除**：

```html
                <div class="mb-3">
                    <label class="form-label fw-semibold text-secondary small">身份选择</label>
                    <select class="form-select" name="role">
                        <option value="">选择身份</option>
                        <option value="1">普通成员</option>
                        <option value="2">管理员</option>
                    </select>
                </div>
```

> 为什么删：身份是**权限信息**。让浏览器提交 `role=2` 再由服务端相信，等于埋伏了一个越权入口。
> 角色必须由服务端按 `phone` 查库后自己写进 session。

### 9.2 `templates/login_t.html`：表单里补 CSRF token

把 `<form method="post">` 改成：

```html
            <form method="post">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

下面那段 `{% if error %}` 的报错块**已经有了，不用改** —— `login()` 视图就是靠它显示"手机号或密码错误"。

### 9.3 `templates/layout_t.html`：表单补 CSRF token（"导入数据"按钮）

```html
            <li><form method="post" action="/clear_input" class="m-0">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="dropdown-item">
                        <i class="bi bi-upload me-2 text-success"></i>导入数据
                    </button>
                </form>
            </li>
```

### 9.4 `templates/layout_t.html`：右侧用户区接上真实数据 + 退出登录

把原来第 53–65 行那段 `<li class="nav-item dropdown"> ... </li>` **整体替换**成：

```html
        <li class="nav-item dropdown">
          <a class="nav-link dropdown-toggle text-white d-flex align-items-center" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">
            <i class="bi bi-person-circle fs-5 me-2 text-white-50"></i>
            {{ current_user.username if current_user else '未登录' }}
          </a>
          <ul class="dropdown-menu dropdown-menu-end border-0 shadow rounded-3 mt-2">
            <li><a class="dropdown-item" href="#">电话：{{ current_user.phone if current_user else '-' }}</a></li>
            <li><a class="dropdown-item" href="#">用户ID：{{ current_user.id if current_user else '-' }}</a></li>
            <li><hr class="dropdown-divider"></li>
            <li><a class="dropdown-item" href="#">用户身份：{{ current_user.role_name if current_user else '-' }}</a></li>
            <li><hr class="dropdown-divider"></li>
            <li>
              <form method="post" action="/logout" class="m-0">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <button type="submit" class="dropdown-item text-danger">
                  <i class="bi bi-box-arrow-right me-2"></i>退出登录
                </button>
              </form>
            </li>
          </ul>
        </li>
```

> `{{ 用户名 }}` / `{{ 电话 }}` / `{{ id }}` / `{{ 身份 }}` 这四个中文变量名从来没被任何视图传入过，
> 所以一直渲染成空白。`current_user` 由 `auth.py` 的 `app_context_processor` 注入，每个模板都能用。

---

## 10. 步骤 9：测试改动

加 CSRF + 登录校验后，`/clear_input` 的两条路由测试会因为"没有 token / 没登录"而失败 —— **这是预期内的**，
它们恰好证明了防护生效。改法如下。

### 10.1 文件顶部（`ROOT = ...` 那一片）加一个常量

```python
CSRF_TOKEN = 'test-csrf-token'
```

### 10.2 改造已有的两条路由测试

`test_clear_input_route_returns_serializable_json` 里，把

```python
    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    resp = app.test_client().post('/clear_input')
```

换成：

```python
    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        # 模拟"管理员已登录 + 带合法 CSRF token"
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'tester'
            sess['phone'] = '13000000000'
            sess['role'] = 2
            sess['_csrf_token'] = CSRF_TOKEN

        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})
```

`test_clear_input_route_reports_quality_failure` 同样处理，把那两行

```python
    resp = app.test_client().post('/clear_input')
```

换成：

```python
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 2
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})
```

> 别用 `app.config['CSRF_ENABLED'] = False` 图省事：那样等于把新代码的安全逻辑从测试里摘出去了，
> 以后 CSRF 挂了测试也不会红。**塞真 token 比关开关更值。**

### 10.3 新增 4 条测试（追加到文件末尾）

```python
# ------------------------------------------------------- 安全层（P0-2 新增）

def test_write_without_csrf_token_is_rejected(test_engine, monkeypatch):
    """没有 CSRF token 的写请求，必须在进视图之前就被挡掉"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    resp = app.test_client().post('/clear_input')
    assert resp.status_code == 400
    assert 'CSRF' in resp.get_data(as_text=True)


def test_anonymous_write_goes_to_login(test_engine, monkeypatch):
    """token 合法但没登录 -> 302 跳登录页（而不是 400）"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})

    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_non_admin_write_is_forbidden(test_engine, monkeypatch):
    """普通成员（role=1）不能触发导入 —— 认证解决"你是谁"，授权解决"你能干什么" """
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['role'] = 1
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/clear_input', data={'csrf_token': CSRF_TOKEN})

    assert resp.status_code == 403


def test_login_ignores_client_supplied_role(test_engine, monkeypatch):
    """客户端谎报 role=2 也没用：角色只认数据库里的值"""
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from sqlalchemy import text as sql_text
    from werkzeug.security import generate_password_hash

    with test_engine.begin() as conn:
        conn.execute(sql_text(
            'INSERT INTO users (phone, username, password_hash, role) '
            "VALUES ('13700000001', '普通成员小李', :h, 1)"),
            {'h': generate_password_hash('secret123')})

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN
        resp = client.post('/login', data={
            'mobile': '13700000001',
            'pwd': 'secret123',
            'role': '2',                 # ← 谎报管理员
            'csrf_token': CSRF_TOKEN,
        })
        assert resp.status_code == 302   # 登录成功
        with client.session_transaction() as sess:
            assert sess['role'] == 1     # 但角色仍是库里的 1
```

```python
def test_login_then_import_end_to_end(test_engine, loaded, monkeypatch):
    """
    端到端：打开登录页 → 登录 → 打开业务页 → 导入成功。

    这条测试顺带锁住一个行为：登录会**轮换 CSRF token**（防会话固定攻击），
    所以登录之前渲染出来的旧表单会失效。浏览器登录后必然重新渲染页面、
    拿到新 token，正常流程无感 —— 但如果你把 token 写死在代码里，这里就会红。
    """
    monkeypatch.setattr(dbmod, 'engine', test_engine)

    from sqlalchemy import text as sql_text
    from werkzeug.security import generate_password_hash

    with test_engine.begin() as conn:
        conn.execute(sql_text(
            'INSERT INTO users (phone, username, password_hash, role) '
            "VALUES ('13700000002', '管理员老张', :h, 2)"),
            {'h': generate_password_hash('admin123')})

    from views import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        # 1. 打开登录页：页面渲染时会把 token 写进 session 和 HTML
        with client.session_transaction() as sess:
            sess['_csrf_token'] = CSRF_TOKEN

        # 2. 提交登录
        resp = client.post('/login', data={'mobile': '13700000002',
                                           'pwd': 'admin123',
                                           'csrf_token': CSRF_TOKEN})
        assert resp.status_code == 302

        # 3. 跟随跳转打开业务页（等价于浏览器重新渲染，layout_t.html 会重新出 token）
        resp = client.get('/customer/list')
        assert resp.status_code == 200, '登录后业务页应该能打开（顺带验证 layout 模板没写坏）'

        # 4. token 确实换过了
        with client.session_transaction() as sess:
            new_token = sess['_csrf_token']
        assert new_token != CSRF_TOKEN

        # 5. 用新 token 触发管理员操作
        resp = client.post('/clear_input', data={'csrf_token': new_token})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'success'
```

> `test_engine` 是 module 级 fixture，临时库里已有别人插过的手机号（`13900000001` / `13800000000`），
> 所以这里用 `13700000001` / `13700000002` 避开 UNIQUE 冲突。

---

## 11. 步骤 10：跑起来验收

```bash
# 1. 建一个管理员账号（交互式输入手机号 / 显示名 / 密码）
flask --app app create-admin

# 2. 跑测试（应该全绿）
pytest -v

# 3. 启动
python app.py
```

浏览器验收清单：

| # | 操作 | 期望 |
|---|---|---|
| 1 | 直接访问 `http://127.0.0.1:5000/` | 跳到 `/login`，地址栏带 `?next=/customer/list` |
| 2 | 输错密码 | 页面顶部红条"手机号或密码错误"，HTTP 401 |
| 3 | 用刚建的账号登录 | 进入客户列表，右上角显示**你的显示名**，下拉里电话 / ID / 身份都有值 |
| 4 | 点"数据分析 → 导入数据" | 返回 JSON（`status: success`） |
| 5 | 点右上角"退出登录" | 回到登录页；再访问 `/customer/list` 又被弹回登录页 |
| 6 | 用普通成员账号（`--role 1` 建）登录后点"导入数据" | 页面显示"需要管理员权限。" |
| 7 | 打开 F12 → Application → Cookies | `session` cookie 有 `HttpOnly` 和 `SameSite=Lax` |

**故障排查**

| 现象 | 原因 |
|---|---|
| 每次重启都要重新登录 | 没在 `.env` 里设 `SECRET_KEY`，看启动日志里的 WARNING |
| 登录后立刻又跳回登录页 | `SECRET_KEY` 每次请求都变了（极端情况：多进程各自随机生成）→ 必须写进 `.env` |
| 表单提交报 "CSRF 校验失败" | 模板里漏了 `csrf_token` 隐藏域，或登录页是缓存下来的旧 HTML → 强刷一次 |
| `flask: command not found` | 用 `python -m flask --app app create-admin` |

---

## 12. 面试讲法（直接对应 `docs/02-面试题库.md`）

一句话版本：**"这个项目的导入接口原本是个挂在导航栏上的 GET 链接，点一下清空三张表重灌。
我做了三层修复：改 POST 改对了 HTTP 语义，加 CSRF 挡住跨站伪造，加管理员权限做授权分级 ——
过程中最值得说的是我一开始把角色放在登录页让用户自己选，后来意识到那等于把越权入口摆在门口。"**

可能被追问的点，答案都在上面的代码里：

| 问题 | 一句话答法 |
|---|---|
| 为什么 `login_required` 要加 `@wraps`？ | 不写会丢掉 `__name__`，多个视图都叫 `wrapper` 时 Flask 注册端点会冲突 |
| CSRF 的原理是什么？ | 浏览器会自动带 cookie，所以跨站表单能"以你的身份"发请求；防御是要求一个**攻击者拿不到**的随机值 —— 它存在 session 里、只出现在你自己的页面 HTML 上 |
| 为什么用 `hmac.compare_digest` 而不是 `==`？ | `==` 在第一个不同字符处返回，理论上能用响应时间差逐字节猜出 token |
| 为什么登出要用 POST？ | 登出改变服务端状态，GET 能被 `<img src="/logout">` 跨站触发 |
| session 里存了什么？为什么不存密码？ | 只存 `user_id / username / phone / role`；密码只在登录那一刻用于 `check_password_hash`，之后不再需要 |
| 登录会不会把 CSRF token 弄失效？ | 会，而且**是故意的**：登录时 `session.clear()` 防会话固定，token 跟着轮换，登录前渲染的旧表单失效。浏览器登录后必然重新渲染页面拿到新 token，所以正常流程无感。这个行为我写了一条端到端测试锁住 |
| 有什么代价？ | 权限改动（比如降级）要重新登录才生效，因为角色是登录时快照进 session 的。要即时生效就得每请求查一次库，那是拿性能换一致性 |
| 为什么不用 Flask-Login / Flask-WTF？ | 这个规模的系统，手写一遍能讲清每一行；Flask-Login 多管的是"记住我""用户加载器"这些，Flask-WTF 的 `CSRFProtect` 本质上就是我现在写的这个 `before_request` 钩子。真上生产我会直接用它们，但面试里手写一遍更说明我懂原理 |

---

## 13. 验证记录（这些代码交付前已经真跑过）

上面 `views/auth.py` / `config.py` 的代码，是从本文档里**原样抽出**后，配一个最小骨架
（sqlite 顶替 MySQL、模板只留 `csrf_token()` 和 `current_user`）实际运行验证过的，
**21 项检查全部通过**：

```
[PASS] 1  未登录访问受保护页 -> 302 跳登录并带 next
[PASS] 2  GET /login 渲染 200 且带 CSRF token
[PASS] 3  无 CSRF token 的 POST -> 400
[PASS] 3b token 不对 -> 400
[PASS] 4  密码错误 -> 401 且提示统一文案
[PASS] 4b 不存在的手机号 -> 同样的 401 文案（不泄露账号是否存在）
[PASS] 5  谎报 role=2 无效：session 里仍是库里的 role=1
[PASS] 5b 登录响应带 HttpOnly + SameSite=Lax 的 session cookie
[PASS] 6  登录后 /customer/list 200
[PASS] 6b layout 的显示名/身份/电话由 current_user 注入
[PASS] 7  普通成员触发管理员操作 -> 403
[PASS] 8  登出 -> 302 回登录页
[PASS] 8b 登出后受保护页再次拦截
[PASS] 9  管理员登录成功并跳客户列表
[PASS] 9b 管理员带新 token 触发导入 -> 200
[PASS] 9c 登录前渲染的旧表单失效（token 已轮换）→ 400
[PASS] 10 ?next=https://evil.com/hack 被拒绝，跳回站内
[PASS] 10 ?next=//evil.com/hack 被拒绝，跳回站内
[PASS] 10 ?next=http://evil.com 被拒绝，跳回站内
[PASS] 10b 合法 next 正常跳转
[PASS] 11 停用账号（is_active=2）-> 403 且提示已停用
==== 21/21 passed ====
```

**验证中发现并修掉的一处问题**：最初 `_save_login()` 只写 `session.clear()`，
CSRF token 被顺带清掉，下一个页面渲染时才惰性生成。行为上没错，但对读代码的人不透明。
现在显式写上 `session['_csrf_token'] = secrets.token_urlsafe(32)`，并配了 9c 号检查
和那条端到端测试把它固定住。

> 关于实物环境：这套逻辑要用到你项目的 MySQL 与蓝图注册，**在你的机器上跑一次
> `pytest -v` 才算最终确认**。命令和验收清单在下面。

---

## 14. 交付前自查

```bash
pytest -v                     # 期望：原有的 20+ 条全绿，外加新增的 5 条安全测试
flask --app app create-admin  # 建一个管理员
python app.py                 # 按第 11 节的表格点一遍
```
