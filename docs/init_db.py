# -*- coding: utf-8 -*-
r"""初始化开发数据库：建库 + 导入 schema.sql + 幂等补齐 P2 字段（PyMySQL 版）

【为什么需要这个脚本】
  本机 mysql.exe（phpstudy 自带的 MySQL 8.0.12 精简客户端）缺认证插件 DLL，
  一连接就报 ERROR 2059，没法执行 `mysql taobao_user < schema.sql`。
  项目本身用 PyMySQL（纯 Python 认证，不依赖 DLL），所以用这个脚本代替。

  典型症状：已经能用 PyMySQL 连上数据库，但报
      (1146, "Table 'taobao_user.users' doesn't exist")
  说明库存在但一张表都没有 —— 就是还没执行过 schema.sql。

【用法】在项目根目录执行：
    E:\\conda_envs\\data_analysis\\python.exe docs\\init_db.py

  连接参数自动读项目根目录的 .env（和 Flask 用的是同一份 Config）。
  想连别的库可以改环境变量 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。

【重置业务数据（每轮验收后恢复"可导入"状态）】
  验收测试会手工加商品、注册客户、下单，导入守卫会因此拒绝 CSV 全量导入。
  验证完想重新灌数据时：
      E:\conda_envs\data_analysis\python.exe docs\init_db.py --reset
      E:\conda_envs\data_analysis\python.exe docs\init_db.py --reset-all
  --reset      只删 CSV 之外产生的数据（role=0 账号、REG- 客户、SKU- 商品、非 T 订单），
               保留管理员账号和已导入的 CSV 数据；
  --reset-all  清空全部业务数据（含已导入的 CSV），保留员工/管理员账号。
  两个命令都保留员工/管理员账号，不用重新建管理员。

【幂等性】
  schema.sql 里全是 CREATE TABLE IF NOT EXISTS，本脚本也是"缺什么补什么"，
  可以反复执行：新库 = 建表；老库 = 只补 P2 新增的列/唯一键/外键。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 让脚本能 import 到项目里的 config

try:
    import pymysql
except ImportError:                    # pragma: no cover
    sys.exit("缺少 PyMySQL：请用项目环境运行，例如\n"
             r"  E:\conda_envs\data_analysis\python.exe docs\init_db.py")

from config import Config              # noqa: E402  （必须在 sys.path 修补之后）

SCHEMA_SQL = ROOT / 'schema.sql'


# ---------------------------------------------------------------- 连接工具

def parse_args():
    parser = argparse.ArgumentParser(
        description='初始化/重置 taobao_user 数据库（PyMySQL 版，替代坏掉的 mysql.exe）')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--reset', action='store_true',
                       help='只删业务数据（客户账号/REG-客户/SKU-商品/非T订单），'
                            '保留管理员账号和已导入的 CSV')
    group.add_argument('--reset-all', action='store_true',
                       help='清空全部业务数据（含已导入 CSV），保留员工/管理员账号')
    parser.add_argument('--yes', action='store_true', help='重置时跳过二次确认')
    return parser.parse_args()


def connect(database=None):
    """连 MySQL。database=None 表示只连服务器（用来 CREATE DATABASE）。"""
    return pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT),
                           user=Config.DB_USER, password=Config.DB_PASSWORD,
                           database=database, charset='utf8mb4',
                           autocommit=True)


def schema_statements():
    """把 schema.sql 拆成一条条可执行语句（和 tests 里的做法一致）。

    schema.sql 只有 "整行以 -- 开头" 的注释需要去掉；列尾的 `-- 注释`
    MySQL 自己认识，保留没问题。
    """
    raw = SCHEMA_SQL.read_text(encoding='utf-8')
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith('--')]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def _count_one(cur, sql, args):
    cur.execute(sql, args)
    return cur.fetchone()[0] > 0


def _column_exists(cur, db, table, column):
    return _count_one(cur, "SELECT COUNT(*) FROM information_schema.COLUMNS "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                      (db, table, column))


def _index_exists(cur, db, table, index):
    return _count_one(cur, "SELECT COUNT(*) FROM information_schema.STATISTICS "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s",
                      (db, table, index))


def _fk_exists(cur, db, table, name):
    return _count_one(cur, "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                           "WHERE CONSTRAINT_SCHEMA=%s AND TABLE_NAME=%s "
                           "AND CONSTRAINT_NAME=%s AND CONSTRAINT_TYPE='FOREIGN KEY'",
                      (db, table, name))


# ---------------------------------------------------------------- 各步骤

def create_database():
    """第 1 步：建库（已存在就跳过）。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{Config.DB_NAME}` "
                        "DEFAULT CHARACTER SET utf8mb4")
    finally:
        conn.close()
    print(f"[1/4] 数据库 {Config.DB_NAME} 就绪")


def apply_schema():
    """第 2 步：执行 schema.sql（全部 CREATE TABLE IF NOT EXISTS）。"""
    conn = connect(Config.DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW TABLES')
            before = {row[0] for row in cur.fetchall()}
            for stmt in schema_statements():
                cur.execute(stmt)
            cur.execute('SHOW TABLES')
            after = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    print(f"[2/4] schema.sql 已执行：之前 {len(before)} 张表 → 现在 {len(after)} 张表")


def ensure_p2_fields():
    """第 3 步：幂等补齐 P2 新增的列/键（新库全都有，会自动跳过）。"""
    db = Config.DB_NAME
    conn = connect(db)
    try:
        with conn.cursor() as cur:
            # role 注释（只改注释，重复执行无副作用）
            cur.execute("ALTER TABLE users MODIFY COLUMN role TINYINT UNSIGNED "
                        "NOT NULL DEFAULT 1 "
                        "COMMENT '角色 0:客户,1:普通成员,2:管理员'")

            if not _column_exists(cur, db, 'users', 'customer_id'):
                cur.execute("ALTER TABLE users ADD COLUMN customer_id VARCHAR(64) NULL "
                            "COMMENT '仅 role=0 客户账号有值；员工为 NULL' AFTER role")
                print('      + users.customer_id')
            if not _index_exists(cur, db, 'users', 'uk_users_customer'):
                cur.execute("ALTER TABLE users ADD UNIQUE KEY uk_users_customer "
                            "(customer_id)")
                print('      + uk_users_customer')
            if not _fk_exists(cur, db, 'users', 'fk_users_customer'):
                cur.execute("ALTER TABLE users ADD CONSTRAINT fk_users_customer "
                            "FOREIGN KEY (customer_id) REFERENCES customers (customer_id)")
                print('      + fk_users_customer -> customers')

            if not _column_exists(cur, db, 'orders', 'paid_at'):
                cur.execute("ALTER TABLE orders ADD COLUMN paid_at DATETIME NULL "
                            "COMMENT '线下收款确认时间' AFTER order_status")
                print('      + orders.paid_at')
            if not _column_exists(cur, db, 'orders', 'paid_by'):
                cur.execute("ALTER TABLE orders ADD COLUMN paid_by INT NULL "
                            "COMMENT '确认收款的员工 users.id（审计用）' AFTER paid_at")
                print('      + orders.paid_by')
    finally:
        conn.close()
    print('[3/4] P2 字段检查完成（缺的已补，已有的跳过）')


def reset_business_data(keep_csv=True):
    """删除业务数据，让导入守卫重新放行（员工/管理员账号保留）。

    keep_csv=True ：只删 CSV 之外产生的数据（智能重置，保留已导入的 baseline）；
    keep_csv=False：清空全部客户/商品/订单（想重新灌 CSV 时用）。
    删除顺序按外键依赖来：orders -> users(role=0) -> customers -> products。
    """
    if keep_csv:
        sqls = [
            "DELETE FROM orders    WHERE transaction_id NOT LIKE 'T%'",
            "DELETE FROM users     WHERE role = 0",
            "DELETE FROM customers WHERE customer_id NOT LIKE 'Customer-%'",
            "DELETE FROM products  WHERE product_id  NOT LIKE 'PROD-%'",
        ]
    else:
        sqls = [
            "DELETE FROM orders",
            "DELETE FROM users   WHERE role = 0",
            "DELETE FROM customers",
            "DELETE FROM products",
        ]
    conn = connect(Config.DB_NAME)
    try:
        with conn.cursor() as cur:
            for sql in sqls:
                cur.execute(sql)
                print(f'      {sql} -> {cur.rowcount} 行')
    finally:
        conn.close()
    print('[reset] 业务数据已清理，可以重新点"导入数据"了')


def print_summary():
    """第 4 步：打印表清单和行数，给下一步操作提示。"""
    conn = connect(Config.DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW TABLES')
            tables = [row[0] for row in cur.fetchall()]
            print('\n[4/4] 当前数据库内容')
            for table in tables:
                cur.execute(f'SELECT COUNT(*) FROM `{table}`')
                print(f'      {table:<12} {cur.fetchone()[0]:>6} 行')

            cur.execute('SELECT COUNT(*) FROM users')
            user_count = cur.fetchone()[0]
    finally:
        conn.close()

    print('\n初始化完成。')
    if user_count == 0:
        print('下一步：创建一个后台账号（默认管理员），再启动网站：')
        print('  flask create_admin')
        print('  flask run')
        print('登录后第一件事：点左侧"导入数据"把 CSV 灌进来（此时库里没有业务数据，导入守卫会放行）。')
    else:
        print('库里已有账号，直接 flask run 即可。')


def main():
    args = parse_args()
    if not Config.DB_PASSWORD:
        sys.exit('没读到数据库密码：确认项目根目录有 .env 文件（可复制 .env.example），'
                 '并填写 DB_PASSWORD。')
    print(f'目标：{Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}\n')
    try:
        create_database()
        apply_schema()
        ensure_p2_fields()
        if args.reset or args.reset_all:
            if not args.yes:
                answer = input('确认删除业务数据？输入 yes 继续：').strip().lower()
                if answer != 'yes':
                    sys.exit('已取消，数据库未改动。')
            print('\n[reset] 开始重置业务数据（保留员工/管理员账号）')
            reset_business_data(keep_csv=args.reset)
        print_summary()
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        msg = exc.args[1] if len(exc.args) > 1 else str(exc)
        if code == 1045:
            sys.exit('密码错误（1045 Access denied）：检查 .env 里的 DB_PASSWORD。')
        if code == 2003:
            sys.exit(f'连不上 MySQL：确认 phpstudy 里 MySQL 服务已启动、端口 '
                     f'{Config.DB_PORT} 是否正确。')
        if 'cryptography' in str(msg).lower():
            sys.exit('该账号用 caching_sha2_password 认证，缺少 cryptography：\n'
                     r'  E:\conda_envs\data_analysis\python.exe -m pip install cryptography')
        sys.exit(f'数据库错误（{code}）：{msg}')


if __name__ == '__main__':
    main()
