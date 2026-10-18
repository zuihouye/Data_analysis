-- taobao_user 表结构
--
-- 初始化方式：
--   mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS taobao_user DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
--   mysql -u root -p taobao_user < schema.sql
--
-- 本文件只含 CREATE TABLE，不含 CREATE DATABASE / USE，
-- 因此可以被执行到任意一个已选定的数据库里（集成测试会用到临时库）。

-- 1. 客户表：客户主数据，不含交易信息，也不含登录凭据
--
-- 登录账号刻意不放在这里 —— 详见文件末尾第 4 张表的说明。
CREATE TABLE IF NOT EXISTS customers (
    customer_id   VARCHAR(64) PRIMARY KEY,     -- 客户ID（主键）
    region        VARCHAR(64) NOT NULL,        -- 所在地区
    cus_status    TINYINT UNSIGNED NOT NULL DEFAULT 1  -- 状态 1:正在使用,2:已注销
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户信息表';


-- 2. 商品表：商品基本信息，不含交易相关的数量、金额
CREATE TABLE IF NOT EXISTS products (
    product_id       VARCHAR(64) PRIMARY KEY,  -- 商品ID（主键）
    product_name     VARCHAR(128) NOT NULL,    -- 商品名称
    product_category VARCHAR(64),              -- 商品类别
    ppu              DECIMAL(10, 2) NOT NULL,  -- 商品当前单价（Price Per Unit）
    product_status   TINYINT UNSIGNED NOT NULL DEFAULT 1  -- 状态 1:正在使用,2:已下架
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品信息表';


-- 3. 订单表：核心事实表，关联客户与商品
--
-- 关于 ppu 快照：products.ppu 是"当前单价"，会随调价变化。订单必须记录
-- 交易发生时的实际单价，否则历史订单金额会跟着商品调价而漂移，财务对账就错了。
CREATE TABLE IF NOT EXISTS orders (
    order_id       BIGINT PRIMARY KEY AUTO_INCREMENT,  -- 内部订单自增ID（代理键）
    transaction_id VARCHAR(64) UNIQUE NOT NULL,        -- 业务交易流水号
    order_date     DATE NOT NULL,                      -- 交易日期

    customer_id    VARCHAR(64) NOT NULL,               -- 客户ID（外键）
    product_id     VARCHAR(64) NOT NULL,               -- 商品ID（外键）

    quantity       INT NOT NULL,                       -- 购买数量
    ppu            DECIMAL(10, 2) NOT NULL,            -- 交易发生时的单价快照
    amount         DECIMAL(12, 2) NOT NULL,            -- 交易总金额
    order_status   TINYINT UNSIGNED NOT NULL DEFAULT 1, -- 状态 1:已下单,2:已支付,3:已取消

    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_orders_product  FOREIGN KEY (product_id)  REFERENCES products  (product_id),

    INDEX idx_order_date (order_date),
    INDEX idx_customer   (customer_id),
    INDEX idx_product    (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单事实表';


-- 4. 后台账号表：登录这个系统的是内部员工（见 templates/login_t.html 的
--    "普通成员 / 管理员"），不是客户，所以刻意不建到 customers 的外键。
--
-- 为什么必须独立成表 —— 两者的数据生命周期互斥：
--   customers 来自 CSV 导入，是【全量快照】：/clear_input 会先 DELETE 再 INSERT。
--   users     来自用户手输，是【增量数据】：只增、只改自己那一条。
--
-- 混在一张表里会同时踩两个坑（都实测过）：
--   1. 账号存在 customers 上 —— 每导一次数据，就把所有人的密码和角色
--      冲成默认值（实测：password_hash 由哈希变成 ''）。
--   2. 给 users.customer_id 建指向 customers 的外键 —— 只要有人注册过，
--      导入就报 1451 Cannot delete or update a parent row，整个点不动。
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    phone         VARCHAR(32)  NOT NULL UNIQUE,        -- 手机号，登录标识（对应登录页的"手机号"）
    username      VARCHAR(64)  NOT NULL,               -- 显示名（对应 layout_t.html 的"用户名"）
    password_hash VARCHAR(255) NOT NULL,               -- werkzeug.security 生成的哈希，绝不存明文
    role          TINYINT UNSIGNED NOT NULL DEFAULT 1, -- 角色 1:普通成员,2:管理员
    is_active     TINYINT UNSIGNED NOT NULL DEFAULT 1, -- 状态 1:启用,2:停用
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='后台账号表（员工）';