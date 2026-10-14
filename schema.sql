-- taobao_user 表结构
--
-- 初始化方式：
--   mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS taobao_user DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
--   mysql -u root -p taobao_user < schema.sql
--
-- 本文件只含 CREATE TABLE，不含 CREATE DATABASE / USE，
-- 因此可以被执行到任意一个已选定的数据库里（集成测试会用到临时库）。

-- 1. 客户表：客户基本信息，不含交易信息
CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR(64) PRIMARY KEY,     -- 客户ID（主键）
    region      VARCHAR(64) NOT NULL         -- 所在地区
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户信息表';


-- 2. 商品表：商品基本信息，不含交易相关的数量、金额
CREATE TABLE IF NOT EXISTS products (
    product_id       VARCHAR(64) PRIMARY KEY,  -- 商品ID（主键）
    product_name     VARCHAR(128) NOT NULL,    -- 商品名称
    product_category VARCHAR(64),              -- 商品类别
    ppu              DECIMAL(10, 2) NOT NULL   -- 商品当前单价（Price Per Unit）
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

    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_orders_product  FOREIGN KEY (product_id)  REFERENCES products  (product_id),

    INDEX idx_order_date (order_date),
    INDEX idx_customer   (customer_id),
    INDEX idx_product    (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单事实表';