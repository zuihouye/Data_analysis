-- ============================================================
-- P2 迁移脚本：在【已有数据的库】上执行（不要 DROP 库）
-- 适用场景：schema.sql 还是 P2 之前的版本，库里已有 customers/products/orders/users
--
-- 注意：如果报 Table 'taobao_user.users' doesn't exist（库是空的、还没建表），
--       不要执行本脚本，先运行 docs/init_db.py（建库 + 建表 + 自动补齐 P2 字段）。
--
-- 执行方式（三选一）：
--   1) mysql -u root -p taobao_user < docs/P2-migrate.sql
--   2) 在 MySQL 客户端里：USE taobao_user; SOURCE docs/P2-migrate.sql;
--   3) 如果 mysql.exe 报下面这个错，改用纯 Python 脚本（本机就是这种情况）：
--        ERROR 2059 (HY000): Authentication plugin 'mysql_native_password'
--        cannot be loaded: 找不到指定的模块。
--      原因是 phpstudy 的 MySQL 8.0.12 客户端是精简版，lib\plugin 里缺认证插件 DLL，
--      不是密码错。项目用的 PyMySQL 不受影响，直接跑等价脚本即可（幂等、可重复执行）：
--        E:\conda_envs\data_analysis\python.exe docs\p2_migrate.py
--
-- 幂等性说明：
--   MySQL 的 ADD COLUMN / ADD CONSTRAINT 不支持 IF NOT EXISTS，
--   重复执行会报 "Duplicate column name / Duplicate key name"。
--   如果报这类错，说明该步骤之前已经执行过，跳过即可（不影响后续步骤）。
--
-- 执行前建议备份：
--   mysqldump -u root -p taobao_user > backup_before_p2.sql
-- ============================================================

-- 0. 确认当前库（防止在错误数据库上执行）
SELECT DATABASE() AS current_db;

-- ------------------------------------------------------------
-- 1. users.role：注释补上"0=客户"（只改注释，不改类型与默认值）
-- ------------------------------------------------------------
ALTER TABLE users
  MODIFY COLUMN role TINYINT UNSIGNED NOT NULL DEFAULT 1
  COMMENT '角色 0:客户,1:普通成员,2:管理员';

-- ------------------------------------------------------------
-- 2. users：增加 customer_id（客户账号 -> 客户主数据）
--    员工账号为 NULL；MySQL 的 UNIQUE 允许多个 NULL，所以员工不受唯一约束影响。
--    外键指向 customers(customer_id)，这是"客户注册必须写两张表"的数据库依据。
-- ------------------------------------------------------------
ALTER TABLE users
  ADD COLUMN customer_id VARCHAR(64) NULL
      COMMENT '仅 role=0 客户账号有值；员工为 NULL' AFTER role,
  ADD UNIQUE KEY uk_users_customer (customer_id),
  ADD CONSTRAINT fk_users_customer
      FOREIGN KEY (customer_id) REFERENCES customers (customer_id);

-- ------------------------------------------------------------
-- 3. orders：增加线下收款审计字段
--    paid_at = 确认收款时间；paid_by = 确认收款的员工 users.id。
--    刻意不给 paid_by 建外键：orders 建在 users 之前，建外键会让建表顺序变脆；
--    审计字段用 INT 足够，展示时再 JOIN users 取名。
-- ------------------------------------------------------------
ALTER TABLE orders
  ADD COLUMN paid_at DATETIME NULL
      COMMENT '线下收款确认时间' AFTER order_status,
  ADD COLUMN paid_by INT NULL
      COMMENT '确认收款的员工 users.id（审计用）' AFTER paid_at;

-- ------------------------------------------------------------
-- 4. 验证：应能看到
--    users.customer_id、uk_users_customer、fk_users_customer
--    orders.paid_at、orders.paid_by
-- ------------------------------------------------------------
SHOW CREATE TABLE users;
SHOW CREATE TABLE orders;

-- ------------------------------------------------------------
-- 5. 可选：给"待确认收款"的订单查询加索引（订单量大时有用，小项目可不加）
--    注意：IF NOT EXISTS 在 MySQL 8.0.19+ 的 ADD INDEX 才支持，老版本重复执行会报错。
-- ------------------------------------------------------------
-- ALTER TABLE orders ADD INDEX idx_status_date (order_status, order_date);
