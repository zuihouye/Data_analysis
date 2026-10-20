"""重建演示库：删库 -> 建库 -> 建表 -> 导入 Kaggle CSV -> 建演示账号。

为什么需要这个脚本
------------------
演示/截图/面试演示时常要一份"干净、可复现"的数据。手工操作要记 4 步
（建库、跑 schema.sql、点导入、建管理员），漏一步就报错，所以收进脚本。

用法（在项目根目录、用项目 conda 环境跑）：

    E:\\conda_envs\\data_analysis\\python.exe scripts\\seed_demo.py

默认行为：重建 ``taobao_rfm_demo`` 库，导入
``static/archive/raw_rfm_sales_transactions_(V2).csv``，并建一个管理员
``13800000000 / admin123``。跑完按提示启动即可预览。

把主库也重置一遍（**会清空该库全部数据，包括注册过的客户账号**，慎用）：

    E:\\conda_envs\\data_analysis\\python.exe scripts\\seed_demo.py --db taobao_user --yes

注意：脚本只负责"从零可跑"，不碰业务代码；库名 / CSV / 管理员账号都能用参数覆盖。
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 允许直接 `python scripts/seed_demo.py` 运行（不需要安装成包）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config          # noqa: E402  (必须在 sys.path 调整之后)
from utils import loader           # noqa: E402

DEMO_DB = 'taobao_rfm_demo'
DEMO_PHONE = '13800000000'
DEMO_USERNAME = '演示管理员'
DEMO_PASSWORD = 'admin123'
SCHEMA_SQL = PROJECT_ROOT / 'schema.sql'
DB_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')


def _engine(db_name=None):
    """复用 Config 的连接参数；db_name=None 时连到服务器本身（用于建库/删库）。"""
    url = make_url(Config.db_uri())
    if db_name is None:
        url = url.set(database=None)
    else:
        url = url.set(database=db_name)
    return create_engine(url, isolation_level='AUTOCOMMIT')


def _sql_statements(sql_text):
    """把 schema.sql 拆成单条语句。

    坑：文件头有 `-- mysql -u root -p -e "CREATE DATABASE ...;"` 这种注释，
    直接按分号切会把注释里的分号当成语句结尾 —— 必须先按行去掉 `--` 注释。
    """
    lines = []
    for line in sql_text.splitlines():
        line = re.sub(r'--.*$', '', line)
        if line.strip():
            lines.append(line)
    return [stmt.strip() for stmt in '\n'.join(lines).split(';') if stmt.strip()]


def recreate_database(db_name):
    """DROP + CREATE + 建表。返回指向新库的 engine。"""
    server = _engine()
    with server.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS `{db_name}`'))
        conn.execute(text(
            f'CREATE DATABASE `{db_name}` '
            'DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci'))
    print(f'[1/4] 已重建数据库 {db_name}')

    engine = _engine(db_name)
    statements = _sql_statements(SCHEMA_SQL.read_text(encoding='utf-8'))
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print(f'[2/4] 已执行 {SCHEMA_SQL.name}（{len(statements)} 条语句）')
    return engine


def _reset_counts(engine):
    with engine.connect() as conn:
        return {table: conn.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
                for table in ('customers', 'products', 'orders', 'users')}


def create_admin(engine, phone, username, password):
    """建（或重置）演示管理员账号；已存在就更新密码，方便反复重建。"""
    pwd_hash = generate_password_hash(password)
    with engine.begin() as conn:
        row = conn.execute(
            text('SELECT id FROM users WHERE phone = :phone'), {'phone': phone}
        ).first()
        if row:
            conn.execute(
                text('UPDATE users SET username = :name, password_hash = :hash, '
                     'role = 2, is_active = 1 WHERE id = :id'),
                {'name': username, 'hash': pwd_hash, 'id': row[0]})
            action = '已重置'
        else:
            conn.execute(
                text('INSERT INTO users (phone, username, password_hash, role, is_active) '
                     'VALUES (:phone, :name, :hash, 2, 1)'),
                {'phone': phone, 'name': username, 'hash': pwd_hash})
            action = '已创建'
    print(f'[4/4] 管理员账号{action}：{phone} / {password}（role=2）')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='重建演示库：删库建库 + schema.sql + 导入 CSV + 建演示管理员')
    parser.add_argument('--db', default=DEMO_DB,
                        help=f'目标库名（默认 {DEMO_DB}）')
    parser.add_argument('--csv', default=None,
                        help='CSV 路径（默认 utils.loader.DEFAULT_CSV）')
    parser.add_argument('--phone', default=DEMO_PHONE, help='管理员手机号')
    parser.add_argument('--username', default=DEMO_USERNAME, help='管理员用户名')
    parser.add_argument('--password', default=DEMO_PASSWORD, help='管理员密码')
    parser.add_argument('--yes', action='store_true',
                        help='允许重建非演示库（会清空该库全部数据）')
    args = parser.parse_args(argv)

    db_name = args.db
    if not DB_NAME_RE.match(db_name):
        parser.error(f'库名只能包含字母/数字/下划线：{db_name!r}')
    if db_name != DEMO_DB and not args.yes:
        parser.error(
            f'{db_name} 不是演示库，重建会清空它的全部数据（含注册账号）。'
            f'确认要这么做请再加 --yes。')

    csv_path = Path(args.csv) if args.csv else loader.DEFAULT_CSV
    if not csv_path.exists():
        parser.error(
            f'找不到 CSV：{csv_path}\n'
            '请先从 Kaggle 下载 Raw Sales Dataset for RFM Customer Segmentation，'
            '放到 static/archive/ 下，或加 --csv 指定路径。')
    if not SCHEMA_SQL.exists():
        parser.error(f'找不到建表脚本：{SCHEMA_SQL}')

    print(f'数据源：{csv_path}')
    engine = recreate_database(db_name)

    df, report = loader.process_and_import(csv_path=csv_path, db_engine=engine)
    print(f'[3/4] 已导入 {len(df)} 行'
          f'（原始 {report["total"]} 行，丢弃 {report["dropped"]} 行脏数据）')

    create_admin(engine, args.phone, args.username, args.password)
    counts = _reset_counts(engine)
    print(f'      表内数据：客户 {counts["customers"]:,} / 商品 {counts["products"]:,} '
          f'/ 订单 {counts["orders"]:,} / 账号 {counts["users"]:,}')

    print()
    print('预览方式（项目根目录）：')
    print(f'  {sys.executable} app.py --db {db_name}')
    print('  或先设环境变量再启动（仅对同一个终端会话有效）：')
    print(f"  $env:DB_NAME='{db_name}'; {sys.executable} app.py")
    print('然后打开 http://127.0.0.1:5000 ，用上面的管理员账号登录。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
