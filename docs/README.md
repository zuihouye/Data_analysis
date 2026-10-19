# RFM 电商客户价值分析平台

基于 **Flask + MySQL + pandas** 的电商交易数据管理与 RFM 客户价值分析系统。

把原始交易明细 CSV 清洗、校验、入库，再按回看窗口（近 3 / 6 / 12 个月或全部）计算每位客户的 **R / F / M** 三个维度并打分分层，
回答一个具体业务问题：**这批客户里，哪些值得重点维护、哪些正在流失。**

> 这是一个求职作品项目，刻意做成一条完整链路：
> 数据接入 → 质量校验 → 事务入库 → 指标计算 → Web 展示 → 测试锁定。
> 重点不在"能跑"，而在**每个工程决策都有理由**（见 `docs/01-改造路线图.md`）。

---

## 一、功能

| 模块 | 能力 |
|---|---|
| 数据接入 | 读取原始交易 CSV，自动统一列名、去千分位、解析日期 |
| 数据质量校验 | 必填列缺失直接中止；重复交易号、金额不一致、异常值按阈值报告并过滤，结果以 JSON 报告返回 |
| 入库 | 单事务内 `DELETE` + `to_sql(append)`，任一步失败整体回滚；重复导入幂等 |
| 客户 / 订单 / 商品 | 列表查询；订单列表关联商品名称 |
| RFM 分析 | 按回看窗口计算 R/F/M 并分箱打分，输出 RFM 组合码；支持近 3 / 6 / 12 个月 / 全部四种窗口切换 |
| 登录与权限 | 手机号 + 密码登录（werkzeug 哈希）、会话管理、CSRF 防护、普通成员 / 管理员分级 |
| 支付 / 并发控制 | Redis 锁（token + TTL + Lua 原子释放）+ `@idempotent` 幂等（同 `X-Request-Id` 只执行一次）；支付 / 取消走条件 `UPDATE` 状态机，重复操作靠 `rowcount` 识别 |
| 测试 | pytest（55 条）：数据质量、事务原子性、外键约束、表结构契约、RFM 分箱、路由层与安全（CSRF / 权限）、Redis 锁与状态机、幂等缓存 |

---

## 二、技术栈

| 层 | 选型 |
|---|---|
| Web 框架 | Flask 3.1（应用工厂 + Blueprint 分模块） |
| 数据处理 | pandas 3.0、numpy |
| 数据库 | MySQL 8（utf8mb4）+ SQLAlchemy Core/Engine + PyMySQL |
| 配置 | python-dotenv（敏感信息全部走 `.env`，不入库不入仓） |
| 测试 | pytest（含临时数据库的集成测试） |
| 前端 | Bootstrap 5 + 原生 Jinja2 模板 |

**为什么是 Flask 而不是 Django / FastAPI**：这个项目的核心是数据分析链路，业务表只有 4 张，
Django 的 ORM / Admin / 迁移体系在这里是负担，而且会遮住 SQL 与事务的细节；
FastAPI 更适合纯接口服务，本项目要的是服务端渲染的后台页面。Flask 的体积最小、
每层都可以自己决定用什么，正好把「连接池怎么配」「事务怎么开」这些面试会问的点暴露出来自己处理。

---

## 三、目录结构

```
Flask_app/
├── app.py                  # 入口：app = create_app()
├── config.py               # 配置集中处：数据库连接串、SECRET_KEY、会话与 CSRF 开关
├── conftest.py             # pytest 共享 fixture（临时目录等）
├── schema.sql              # 全部建表 DDL（不含 CREATE DATABASE，可执行到任意库）
├── requirements.txt
├── .env.example            # 配置模板（真实的 .env 不入仓）
├── views/                  # 路由层（Blueprint）
│   ├── __init__.py         #   应用工厂 create_app() + CLI 命令
│   ├── auth.py             #   登录 / 登出 / login_required / CSRF
│   ├── clear_input.py      #   数据导入（写操作，仅管理员）
│   ├── ge_RFM.py           #   RFM 分析与页面
│   ├── order.py            #   订单 / 商品
│   └── user.py             #   客户
├── utils/                  # 业务与数据层
│   ├── db.py               #   连接池（engine）
│   ├── loader.py           #   清洗 → 校验 → 入库 的编排与事务
│   └── quality.py          #   数据质量规则与报告
├── templates/              # Jinja2 模板
├── static/                 # Bootstrap、jQuery、示例数据（archive/）
├── tests/                  # pytest 用例
└── docs/
    ├── 01-改造路线图.md     # 每个改造点的原因、取舍与面试讲法
    └── 02-面试题库.md       # 结合本项目的面试问答准备
```

**分层原则**：`views/` 只负责解析请求与渲染响应；清洗、校验、事务全部在 `utils/`；
`templates/` 不含业务判断。这样路由层薄到可以单独测试，数据处理能脱离 HTTP 复用。

---

## 四、快速开始

### 1. 准备数据库

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS taobao_user DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
mysql -u root -p taobao_user < schema.sql
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```ini
DB_USER=root
DB_PASSWORD=你的数据库密码
DB_HOST=localhost
DB_PORT=3306
DB_NAME=taobao_user
SECRET_KEY=用下面的命令生成
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> `.env` 已在 `.gitignore` 中，密码不会进版本库。
> 未设置 `SECRET_KEY` 时程序会临时生成一个随机值并告警 —— 能跑，但**每次重启所有登录都会失效**。

### 3. 安装依赖并建账号

```bash
pip install -r requirements.txt

# 创建后台账号（默认管理员，交互式输入手机号 / 显示名 / 密码）
flask --app app create-admin
```

### 4. 启动

```bash
python app.py
# 或带调试器：
flask --app app run --debug
```

浏览器打开 `http://127.0.0.1:5000` → 跳转登录页 → 登录后在「数据分析 → 导入数据」载入示例 CSV。

### 5. 跑测试

```bash
pytest -v
```

集成测试会在 MySQL 中创建**临时库**、建表、灌数据，结束后自动清理，不会污染 `taobao_user`。

---

## 五、数据模型

```
customers (客户主数据)          products (商品主数据)
   customer_id  PK                 product_id  PK
   region                          product_name / product_category
   cus_status                      ppu（当前单价）
        │                                │
        └──────────┬─────────────────────┘
                   ▼
                orders (交易事实表)
                   order_id       PK  自增代理键
                   transaction_id UNIQUE 业务流水号
                   order_date / customer_id FK / product_id FK
                   quantity / ppu（成交时单价快照） / amount
                   INDEX(order_date) INDEX(customer_id) INDEX(product_id)

                users (后台账号表，与客户无关)
                   id PK / phone UNIQUE / username / password_hash / role / is_active
```

三个刻意的设计决策，也都是面试可讲点：

1. **`orders.ppu` 是快照，不是外键引用。** `products.ppu` 是"当前价"，会随调价变化；
   订单必须记录成交当时的单价，否则历史订单金额会跟着调价漂移，财务对账就错了。
2. **`orders` 用自增 `order_id` 作主键，`transaction_id` 只加 UNIQUE。** 业务流水号可能变长、
   可能来自外部系统，用它当主键会让所有外键跟着变宽；代理键 + 唯一约束是更稳的做法。
3. **后台账号独立成 `users` 表，且不与 `customers` 建外键。** 两者的数据生命周期互斥：
   `customers` 来自 CSV 全量重灌（先删后插），`users` 是手输的增量数据。
   混在一起会出现"导一次数据把所有人的密码冲成默认值"；给 `users` 加指向 `customers`
   的外键则会让导入直接报 1451 而点不动。系统里登录的是内部运营（有"管理员"身份），
   不是客户 —— 这两张表本来就不该有关联。

---

## 六、RFM 计算口径

**基准日 = 数据集里最新的订单日**（不是 `today`）：历史数据用"今天"当基准，会让所有客户的 R 同时变大，跨年数据完全失真。

**观察窗口**支持「近 3 个月 / 近 6 个月 / 近一年 / 全部」四种，页面右上角可切换。**先切窗口、再在窗口内排名分箱** ——
顺序反了得到的是"全量排名的一个子集"，而不是"窗口内的排名"（实测近 3 个月窗口下 93.5% 的客户分组会被改写）。

| 指标 | 本项目口径 | 打分 |
|---|---|---|
| **R**（Recency） | `R = 基准日 − 客户窗口内最后一次交易日期`（天），越小越近 | 先 `rank(method='first')` 打散再 `qcut` 三等分，R 越小分越高（3/2/1） |
| **F**（Frequency） | 窗口内**交易笔数**（`COUNT(transaction_id)`），不是购买件数 | 默认与 R/M 一致等频三档；可切到固定业务阈值 `cut([0,1,3,∞))` → 1 / 2 / 3 |
| **M**（Monetary） | 窗口内消费总额 `SUM(amount)` | 先 `rank(method='first')` 打散再 `qcut` 三等分 → 1 / 2 / 3 |

- **为什么分箱前要先 `rank(method='first')`**：R/M 的重复值极多（很多客户最后一次购买是同一天），
  直接 `qcut` 会在重复边界上报 `Bin edges must be unique`；而常被推荐的 `duplicates='drop'`
  在带 `labels` 参数时同样报错（标签数比箱数多一个）。rank 让值唯一，边界永不重复。
- F 用**交易笔数**而不是**购买件数**：频次衡量的是"来店次数"，一次买 100 件和分 100 次买
  在客户价值上是两回事。
- 三项得分拼接为 RFM 组合码（如 `333`），共 27 种组合。

---

## 七、接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET/POST | `/login` | 登录页 / 提交登录 | 匿名 |
| POST | `/logout` | 退出登录 | 登录 |
| GET | `/customer/list` | 客户列表 | 登录 |
| GET | `/order/list` | 订单列表（关联商品名） | 登录 |
| GET | `/product/list` | 商品列表 | 登录 |
| GET | `/ge_rfm?window=` | RFM 分析结果页（`window` 取 `3m` / `6m` / `12m` / `all`） | 登录 |
| POST | `/clear_input` | 清空并重新导入交易数据 | **管理员** |

---

## 八、已知限制与后续计划

按优先级：

- [ ] **CRUD 补全**：`/customer/update|delete`、`/product/add|update|delete`、`/order/update|delete`
      目前是空壳（返回 500）。删除客户会撞上外键约束，计划改成**软删除**（订单是财务凭证，不做物理删除）。
- [ ] **客户分层命名**：把 `333` 这样的组合码映射成"重要价值客户 / 流失预警客户"等业务标签。
- [ ] **统一异常处理**：`IntegrityError` / 404 / 500 的自定义页面与日志。
- [ ] **多格式接入**：`static/archive/` 下的另一种 CSV 结构（30000 行版本）暂不支持，
      计划用「新增一个 loader 而不改已有代码」的方式扩展。
- [ ] **物化 RFM 结果表 + 后端分页**：当前每次访问实时计算，数据量上去后需要预计算。

---

## 九、项目文档

- [`docs/01-改造路线图.md`](docs/01-改造路线图.md)：每个改造点的**原因、方案取舍、面试讲法**
- [`docs/02-面试题库.md`](docs/02-面试题库.md)：结合本项目的面试问答准备（项目类 + 八股 + HR）
