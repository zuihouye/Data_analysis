# -*- coding: utf-8 -*-
"""P2 数据库迁移（PyMySQL 版，替代 mysql.exe）

【为什么需要这个脚本】
  本机 MySQL 客户端是 phpstudy 自带的 MySQL 8.0.12，属于精简版：它的
  lib\\plugin 目录里没有 mysql_native_password 的认证插件 DLL，所以
  mysql.exe 一连接就报：
      ERROR 2059 (HY000): Authentication plugin 'mysql_native_password'
      cannot be loaded: 找不到指定的模块。
  这不是密码错、也不是 SQL 错，而是"客户端缺插件 DLL"。
  项目本身用的是 PyMySQL（纯 Python 实现认证，不依赖这些 DLL），所以能连上；
  这个脚本就复用项目环境里的 PyMySQL，把 docs/P2-migrate.sql 里的 ALTER 做一遍。

【用法】
  如果报 "Table 'taobao_user.users' doesn't exist"（库是空的、还没建表），
  先运行 docs/init_db.py 建库建表，再回来跑本脚本。

  方式一（交互输入密码，推荐）：
      E:\\conda_envs\\data_analysis\\python.exe docs\\p2_migrate.py

  方式二（先设环境变量，避免每次输入）：
      PowerShell:
          $env:DB_PASSWORD='你的MySQL密码'
          E:\\conda_envs\\data_analysis\\python.exe docs\\p2_migrate.py

  连接参数默认：host=127.0.0.1 port=3306 user=root database=taobao_user，
  需要改就加参数，例如：
      ... docs\\p2_migrate.py --host localhost --user root --database taobao_user

【幂等性】
  脚本先查 information_schema，已存在的列/唯一键/外键会跳过，可以安全地
  重复执行；执行完会打印最终的表结构用于核对。

【做了什么】
  users:  role 注释改为 0 客户 / 1 员工 / 2 管理员
          + customer_id VARCHAR(64) NULL（仅客户账号有值）
          + UNIQUE KEY uk_users_customer
          + FOREIGN KEY fk_users_customer -> customers(customer_id)
  orders: + paid_at DATETIME NULL（确认收款时间）
          + paid_by INT NULL（确认收款的员工用户 id）
"""
import argparse
import getpass
import os
import sys

try:
    import pymysql
except ImportError:  # pragma: no cover
    sys.exit("缺少 PyMySQL：请用项目环境 E:\\conda_envs\\data_analysis\\python.exe 运行本脚本。")


def parse_args():
    p = argparse.ArgumentParser(description="P2 数据库迁移（幂等）")
    p.add_argument("--host", default=os.getenv("DB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    p.add_argument("--user", default=os.getenv("DB_USER", "root"))
    p.add_argument("--database", default=os.getenv("DB_NAME", "taobao_user"))
    p.add_argument("--password", default=None,
                   help="不建议用：默认取环境变量 DB_PASSWORD，没有就交互输入")
    return p.parse_args()


def column_exists(cur, db, table, column):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (db, table, column))
    return cur.fetchone()[0] > 0


def index_exists(cur, db, table, index):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s",
        (db, table, index))
    return cur.fetchone()[0] > 0


def fk_exists(cur, db, table, name):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s "
        "AND CONSTRAINT_TYPE='FOREIGN KEY'",
        (db, table, name))
    return cur.fetchone()[0] > 0


def step(conn, cur, db, label, check, ddl, hard=True):
    """执行一步：check 为真则跳过；否则执行 ddl。hard=False 时失败只警告。"""
    exists = check(cur, db) if check else False
    if exists:
        print(f"  [跳过] {label}（已存在）")
        return True
    try:
        cur.execute(ddl)
        print(f"  [完成] {label}")
        return True
    except pymysql.MySQLError as exc:
        print(f"  [失败] {label}: {exc}")
        if hard:
            raise
        return False


def main():
    args = parse_args()
    password = args.password
    if password is None:
        password = os.getenv("DB_PASSWORD")
    if password is None:
        password = getpass.getpass(f"MySQL 用户 {args.user} 的密码（输入不显示）: ")

    print(f"目标：{args.user}@{args.host}:{args.port}/{args.database}\n")
    try:
        conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                               password=password, database=args.database,
                               charset="utf8mb4", autocommit=True)
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        msg = exc.args[1] if len(exc.args) > 1 else str(exc)
        if "cryptography" in str(msg).lower():
            sys.exit("该账号使用 caching_sha2_password 认证，需要 cryptography 包：\n"
                     "  E:\\conda_envs\\data_analysis\\python.exe -m pip install cryptography\n"
                     "装完再重新运行本脚本。")
        if code == 1045:
            sys.exit("密码错误（1045 Access denied），请重新运行并输入正确密码。")
        if code == 2003:
            sys.exit(f"连不上 MySQL：确认 phpstudy 里 MySQL 服务已启动、端口 {args.port} 是否正确。")
        if code == 1049:
            sys.exit(f"数据库 {args.database} 不存在：先建库并导入 schema.sql。")
        sys.exit(f"连接失败（错误码 {code}）：{msg}")
    except (RuntimeError, ImportError) as exc:  # pymysql 缺 cryptography 时抛 RuntimeError
        if "cryptography" in str(exc).lower():
            sys.exit("该账号使用 caching_sha2_password 认证，需要 cryptography 包：\n"
                     "  E:\\conda_envs\\data_analysis\\python.exe -m pip install cryptography\n"
                     "装完再重新运行本脚本。")
        raise
    db = args.database
    try:
        with conn.cursor() as cur:
            # 前置检查：四张表必须都在
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s", (db,))
            tables = {row[0] for row in cur.fetchall()}
            missing = {"customers", "products", "orders", "users"} - tables
            if missing:
                sys.exit(f"数据库 {db} 缺少表：{', '.join(sorted(missing))}，"
                         f"请先导入 schema.sql。")

            print("1) users.role 注释")
            step(conn, cur, db, "users.role 注释（0 客户/1 员工/2 管理员）", None,
                 "ALTER TABLE users MODIFY COLUMN role TINYINT UNSIGNED NOT NULL "
                 "DEFAULT 1 COMMENT '角色 0:客户,1:普通成员,2:管理员'", hard=False)

            print("2) users.customer_id")
            step(conn, cur, db, "users.customer_id 列",
                 lambda c, d: column_exists(c, d, "users", "customer_id"),
                 "ALTER TABLE users ADD COLUMN customer_id VARCHAR(64) NULL "
                 "COMMENT '仅 role=0 客户账号有值；员工为 NULL' AFTER role")

            print("3) users.customer_id 唯一键")
            step(conn, cur, db, "唯一键 uk_users_customer",
                 lambda c, d: index_exists(c, d, "users", "uk_users_customer"),
                 "ALTER TABLE users ADD UNIQUE KEY uk_users_customer (customer_id)")

            print("4) users.customer_id 外键")
            step(conn, cur, db, "外键 fk_users_customer -> customers",
                 lambda c, d: fk_exists(c, d, "users", "fk_users_customer"),
                 "ALTER TABLE users ADD CONSTRAINT fk_users_customer "
                 "FOREIGN KEY (customer_id) REFERENCES customers (customer_id)")

            print("5) orders.paid_at / paid_by")
            if column_exists(cur, db, "orders", "paid_at"):
                print("  [跳过] orders.paid_at（已存在）")
            else:
                step(conn, cur, db, "orders.paid_at 列", None,
                     "ALTER TABLE orders ADD COLUMN paid_at DATETIME NULL "
                     "COMMENT '线下收款确认时间' AFTER order_status")
            if column_exists(cur, db, "orders", "paid_by"):
                print("  [跳过] orders.paid_by（已存在）")
            else:
                step(conn, cur, db, "orders.paid_by 列", None,
                     "ALTER TABLE orders ADD COLUMN paid_by INT NULL "
                     "COMMENT '确认收款的员工 users.id（审计用）' AFTER paid_at")

            print("\n=== users 关键列 ===")
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
                "AND TABLE_NAME='users' AND COLUMN_NAME IN "
                "('id','phone','role','customer_id','is_active') ORDER BY ORDINAL_POSITION", (db,))
            for row in cur.fetchall():
                print("  ", row)

            print("\n=== orders 关键列 ===")
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
                "AND TABLE_NAME='orders' AND COLUMN_NAME IN "
                "('order_id','order_status','paid_at','paid_by') ORDER BY ORDINAL_POSITION", (db,))
            for row in cur.fetchall():
                print("  ", row)

        print("\n迁移完成 ✅  接下来：重启 Flask，并在 PyCharm 里跑 pytest。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
