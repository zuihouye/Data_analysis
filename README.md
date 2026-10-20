# 电商订单后台 + RFM 客户价值分析

基于 **Flask + MySQL + Redis + pandas** 的电商交易数据管理与 RFM 客户价值分析系统。

两条线：

1. **业务线（内部员工用）**：把原始交易明细 CSV 清洗、校验、入库，提供客户 / 订单 / 商品的列表查询；
   再按回看窗口（近 3 / 6 / 12 个月或全部）计算每位客户的 **R / F / M** 三个维度并打分分层，
   回答一个具体业务问题：**这批客户里，哪些值得重点维护、哪些正在流失。**
2. **工程线（后端能力示范）**：支付链路的**并发控制地基** —— Redis 分布式锁 + 幂等 + 数据库条件 `UPDATE` 状态机。
   只到接口骨架，不接真实网关（见下面的「为什么」）。

> 这是一个求职作品项目，刻意做成一条完整链路：
> 数据接入 → 质量校验 → 事务入库 → 指标计算 → Web 展示 → 测试锁定。
> 重点不在"能跑"，而在**每个工程决策都有理由**（见 `docs/01-改造路线图.md`）。


## 一、功能

| 模块 | 能力 | 状态 |
|---|---|---|
| 数据接入 | 读取原始交易 CSV，自动统一列名、去千分位、解析日期 | ✅ |
| 数据质量校验 | 必填列缺失直接中止；重复交易号、金额不一致、异常值按阈值报告并过滤，结果以 JSON 报告返回 | ✅ |
| 入库 | 单事务内 `DELETE` + `to_sql(append)`，任一步失败整体回滚；重复导入幂等 | ✅ |
| 客户 / 订单 / 商品 | 列表查询；订单列表关联商品名称；按角色过滤可见数据 | ✅ |
| RFM 分析 | 按回看窗口计算 R/F/M 并分箱打分，输出 RFM 组合码，再折成 8 个经典客户分群（带运营动作）；支持近 3 / 6 / 12 个月 / 全部四种窗口切换 | ✅ |
| 登录与权限 | 手机号 + 密码登录（werkzeug 哈希）、会话管理、CSRF 防护、客户 / 普通成员 / 管理员三档角色 | ✅ |
| 支付 / 并发控制 | Redis 锁（token + TTL + Lua 原子释放）+ `@idempotent` 幂等；取消订单走条件 `UPDATE` 状态机，重复操作靠 `rowcount` 识别；发起支付为 `501` 占位 | ✅ 骨架 |
| 客户下单闭环 | 客户自助注册（`customers` + `users(role=0)` 双表事务）→ 浏览在售商品 → 下单 → 改数量 / 取消 → 员工「确认线下收款」（`1 → 2`）；已支付订单冻结，无退款（P1-B 不做） | ✅ |
| CRUD 与软删除 | 客户注销 / 商品下架 / 订单取消都只改状态列（不物理 `DELETE`）；写接口分别挂 `@customer_required` / `@staff_required` / `@admin_required`，订单操作再做归属校验 | ✅ |
| 测试 | pytest（98 条）：数据质量、事务原子性、外键约束、表结构契约、RFM 分箱与客户分群、RFM 页面聚合、路由层与安全（CSRF / 权限 / 越权）、Redis 锁与状态机、幂等缓存、P2 下单闭环 | ✅ |

---

## 二、技术栈

| 层 | 选型 |
|---|---|
| Web 框架 | Flask 3.1（应用工厂 + Blueprint 分模块） |
| 数据处理 | pandas 3.0、numpy 2.5 |
| 数据库 | MySQL 8（utf8mb4）+ SQLAlchemy Engine + PyMySQL |
| 缓存 / 锁 | Redis（服务端 3.0.5 实测）+ redis-py 6.4（本项目只用锁与幂等，不做业务缓存） |
| 配置 | python-dotenv（敏感信息全部走 `.env`，不入库不入仓） |
| 测试 | pytest（含临时数据库的集成测试、真 Redis 的锁 / 幂等测试） |
| 前端 | 自建设计系统（`static/css` + 原生 JS，零第三方 UI 依赖）+ 原生 Jinja2 模板 |

各依赖的精确版本锁在 `requirements.txt`（含 `redis==6.4.0` 这类兼容性坑的说明）。

**为什么是 Flask 而不是 Django / FastAPI**：这个项目的核心是数据分析链路，业务表只有 4 张，
Django 的 ORM / Admin / 迁移体系在这里是负担，而且会遮住 SQL 与事务的细节；
FastAPI 更适合纯接口服务，本项目要的是服务端渲染的后台页面。Flask 的体积最小、
每层都可以自己决定用什么，正好把「连接池怎么配」「事务怎么开」这些面试会问的点暴露出来自己处理。

---

## 三、目录结构

```
Flask_app/
├── app.py                  # 入口：app = create_app()
├── config.py               # 配置集中处：数据库 / SECRET_KEY / 会话与 CSRF / Redis 与锁参数
├── conftest.py             # pytest 共享 fixture（临时目录等）
├── schema.sql              # 全部建表 DDL（不含 CREATE DATABASE，可执行到任意库）
├── requirements.txt
├── .env.example            # 配置模板（真实的 .env 不入仓）
├── views/                  # 路由层（Blueprint，只解析请求 + 渲染响应）
│   ├── __init__.py         #   应用工厂 create_app() + create_admin CLI + 错误页 / 模板过滤器
│   ├── auth.py             #   登录 / 登出 / login_required / admin_required / CSRF
│   ├── clear_input.py      #   数据导入页与写操作（仅管理员）
│   ├── ge_RFM.py           #   RFM 分析页面（读表 + 调聚合层）
│   ├── order.py            #   订单 / 商品
│   ├── pay.py              #   支付接口骨架（锁 + 幂等 + 状态机）
│   └── user.py             #   客户
├── utils/                  # 业务与数据层（不 import Flask）
│   ├── db.py               #   连接池（engine）
│   ├── loader.py           #   清洗 → 校验 → 入库 的编排与事务
│   ├── quality.py          #   数据质量规则与报告
│   ├── rfm.py              #   RFM 计算（纯函数：DataFrame 进、DataFrame 出）
│   ├── rfm_report.py      #   页面聚合层：KPI / 分群 / 分布 / 环比 / 明细（纯函数）
│   ├── redis_client.py     #   Redis 惰性单例（可重置，测试可替换）
│   ├── lock.py             #   分布式锁（redis_lock / order_lock）
│   └── idempotent.py       #   @idempotent 幂等装饰器
├── templates/              # Jinja2 模板（base.html 基座 + 各页面模板，无第三方 UI 框架）
├── static/                 # 自建设计系统：css/ app.css + rfm.css；js/ app.js + rfm.js + list.js + form.js + import.js
├── scripts/                # seed_demo.py：一键重建演示库（建库 → 建表 → 导入 CSV → 建管理员）
├── tests/                  # pytest 用例（rfm / rfm_report / quality / import_db / lock / idempotent）
└── docs/                   # 项目文档 + frontend-preview/（页面预览截图）
```

**分层原则**：`views/` 只负责解析请求与渲染响应；清洗、校验、事务全部在 `utils/`；
`templates/` 不含业务判断。`utils/rfm.py` 尤其刻意做成**纯函数**（不 import Flask、不碰数据库），
所以它能被 pytest 直接测，也能在 notebook 里复用。

---

## 四、快速开始

### 1. 准备数据库

确保本机 MySQL 服务已启动（页面查询和集成测试都要连它）：

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS taobao_user DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
mysql -u root -p taobao_user < schema.sql
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写（数据库 + 会话密钥 + Redis）：

```ini
DB_USER=root
DB_PASSWORD=你的数据库密码
DB_HOST=localhost
DB_PORT=3306
DB_NAME=taobao_user

SECRET_KEY=用下面的命令生成
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=1                  # 支付锁 / 幂等单独用 db1，跟业务缓存分开
REDIS_PASSWORD=             # 有密码就填
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> `.env` 已在 `.gitignore` 中，密码不会进版本库。
> 未设置 `SECRET_KEY` 时程序会临时生成一个随机值 —— 能跑，但**每次重启所有登录都会失效**。

### 3. 准备 Redis

支付模块依赖 Redis（锁 + 幂等）。本地起一个即可（Windows 可用 Redis 5.x/3.x 的 msi 版）：

```bash
redis-server.exe            # 或作为服务常驻
redis-cli ping              # 期望 PONG
```

> 没起 Redis 也不影响主流程：`/pay/*` 会快速失败，只有锁 / 幂等相关的测试会自动 skip。

### 4. 安装依赖并建账号

```bash
pip install -r requirements.txt

# 创建后台账号（命令行是下划线 create_admin；不带参数会交互式询问）
flask --app app create_admin
# 也可以直接给参数：
# flask --app app create_admin --phone 13800000000 --username 管理员 --password 你的密码 --role 2
```

### 5. 一键重建演示库（可选，推荐）

想直接看页面效果，可以跳过上面的建表与导入，用脚本一次完成「建库 → 建表 → 导入 CSV → 建管理员」：

```bash
python scripts/seed_demo.py
# 等价于：重建 taobao_rfm_demo 库 → 执行 schema.sql → 导入 Kaggle 原始销售数据 → 建管理员
# 管理员：手机号 13800000000 / 密码 admin123（角色 = 管理员）

# 用演示库启动（不设置 DB_NAME 时仍连 .env 里的 taobao_user）
# 推荐：--db 参数在任意终端 / PyCharm「运行」配置里都生效
python app.py --db taobao_rfm_demo

# 也可以先设环境变量再启动（只在当前终端会话内有效，换终端或点 IDE 运行按钮会失效）
# PowerShell:
$env:DB_NAME='taobao_rfm_demo'; python app.py
# cmd:
set DB_NAME=taobao_rfm_demo&& python app.py
```

> 启动时控制台会打印当前实际连接的数据库名（`[*] 数据库: xxx`），
> 如果登录提示账号密码错误，先看这一行是不是你以为的库。

> 脚本默认**只**重建 `taobao_rfm_demo`；把 `--db` 指向别的库名时必须再加 `--yes` 才会执行，
> 避免误删 `taobao_user` 里的数据。`--csv` 可换数据文件，`--phone / --username / --password`
> 可改管理员账号。导入完成后共 812 位客户、1,999 笔已支付订单。

### 6. 启动

```bash
python app.py
# 或带调试器：
flask --app app run --debug
```

浏览器打开 `http://127.0.0.1:5000` → 跳转登录页 → 登录后顶栏是平铺导航：
**导入数据 / 客户管理 / 订单管理 / 产品管理 / 数据分析**（客户账号看到的是 商品中心 / 我的订单）。
管理员先从「导入数据」载入示例 CSV（示例数据在 `static/archive/`，历史流水会按已支付立账），
再点「数据分析」查看 RFM 分层的 KPI、气泡矩阵、分布图和客户明细。

> 客户账号从 `/register` 自助注册（`users.role=0`，同一事务里同时写 `customers` 表）；
> 员工 / 管理员账号用 `flask create_admin` 创建。想完整体验「下单 → 员工确认收款」，
> 建议顺序是：`python scripts/seed_demo.py` 得到演示库 → 管理员导入 CSV → 再注册客户下单。

### 7. 跑测试

```bash
pytest -v
```

- MySQL 相关的集成测试会创建**临时库**、建表、灌数据，结束后自动清理，不会污染 `taobao_user`；
- Redis 相关的测试用 `db1` 且只操作 `idem:unittest:*` 前缀的键，跑完清干净，不会 `FLUSHDB`；
- 本机没起 **MySQL** 或 **Redis** 时，依赖它们的集成用例会自动 skip（不是失败）：
  MySQL 不可用时是入库 / 路由 / 表结构契约那批，Redis 不可用时是锁 / 幂等两组。
  所以 `skipped` 变多通常只是服务没起 —— 两个服务都在时才是完整的 **98 passed**。

---

## 五、数据模型

```
customers (客户主数据)          products (商品主数据)
   customer_id  PK                 product_id  PK
   region                          product_name / product_category
   cus_status                      ppu（当前单价） / product_status
        │                                │
        │  (P2: users.customer_id FK)    │
        └──────────┬─────────────────────┘
                   ▼
                orders (交易事实表)
                   order_id       PK  自增代理键
                   transaction_id UNIQUE 业务流水号（CSV: T… / Web: W…）
                   order_date / customer_id FK / product_id FK
                   quantity / ppu（成交时单价快照） / amount
                   order_status   支付状态机：1 已下单 / 2 已支付 / 3 已取消
                   paid_at / paid_by   线下收款确认时间、操作员工（审计字段）
                   INDEX(order_date) INDEX(customer_id) INDEX(product_id)

                users (账号表：员工 / 管理员 + 客户)
                   id PK / phone UNIQUE / username / password_hash
                   role（0 客户 / 1 普通成员 / 2 管理员） / is_active / created_at
                   customer_id（仅 role=0 有值，UNIQUE，FK → customers）
```

五个刻意的设计决策，也都是面试可讲点：

1. **`orders.ppu` 是快照，不是外键引用。** `products.ppu` 是"当前价"，会随调价变化；
   订单必须记录成交当时的单价，否则历史订单金额会跟着调价漂移，财务对账就错了。
2. **`orders` 用自增 `order_id` 作主键，`transaction_id` 只加 UNIQUE。** 业务流水号可能变长、
   可能来自外部系统，用它当主键会让所有外键跟着变宽；代理键 + 唯一约束是更稳的做法。
3. **账号表与客户主数据分开，但客户账号用 `customer_id` 关联主数据。** P0 阶段登录的是内部运营，
   `users` 与 `customers` 无关；P2 加了客户自助注册后，`role=0` 的账号必须能定位到"是哪个客户"，
   于是加了可空 + UNIQUE 的 `customer_id` 外键（员工为 NULL）。仍然不把密码放进 `customers`，
   因为 `customers` 会被 CSV 全量重灌；重灌风险改由导入守卫 `ensure_import_safe()` 拦截，
   而不是靠"不建外键"。
4. **支付状态用 `order_status` 数字状态机，不用布尔 `is_paid`。** 布尔两态表达不了"已取消"，
   而且状态推进要靠条件 `UPDATE ... WHERE order_status = 1` + `rowcount` 判定，
   这样并发下"取消"和"支付"同时到达时，只会有一个成功，另一个拿到 0 行。
5. **软删除复用业务状态列，不新增 `is_deleted`。** `cus_status` / `product_status` /
   `order_status` 本来就是业务状态，再加一个"删除"标志会让所有查询多一个条件，且语义和
   "注销 / 下架 / 取消"重复。删除客户 = `cus_status=2` + 停用账号；删除商品 = 下架；
   删除订单 = 取消（财务凭证永不物理删）。

---

## 六、RFM 计算口径

**基准日 = 数据集里最新的订单日**（不是 `today`）：历史数据用"今天"当基准，会让所有客户的 R 同时变大，跨年数据完全失真。

**观察窗口**支持「近 3 个月 / 近 6 个月 / 近一年 / 全部」四种，页面右上角可切换。**先切窗口、再在窗口内排名分箱** ——
顺序反了得到的是"全量排名的一个子集"，而不是"窗口内的排名"（实测近 3 个月窗口下 93.5% 的客户分组会被改写）。

| 指标 | 本项目口径 | 打分 |
|---|---|---|
| **R**（Recency） | `R = 基准日 − 客户窗口内最后一次交易日期`（天），越小越近 | 先 `rank(method='first')` 打散再 `qcut` 三等分，R 越小分越高（3/2/1） |
| **F**（Frequency） | 窗口内**交易笔数**（`COUNT(transaction_id)`），不是购买件数 | 默认与 R/M 一致等频三档；`rfm_table(..., f_mode='fixed')` 可切固定业务阈值 `cut([0,1,3,∞))` → 1 / 2 / 3（页面不暴露这个开关） |
| **M**（Monetary） | 窗口内消费总额 `SUM(amount)` | 先 `rank(method='first')` 打散再 `qcut` 三等分 → 1 / 2 / 3 |

- **为什么分箱前要先 `rank(method='first')`**：R/M 的重复值极多（实测 812 个客户里只有 166 个不同的 R 值，
  很多客户最后一次购买是同一天），直接 `qcut` 会在重复边界上报 `Bin edges must be unique`；
  而 `duplicates='drop'` 会把同值客户并进更少的箱，极端情况（全列同值）整列变 `NaN`、静默丢掉全部客户。
  rank 让值唯一，边界永不重复。
  - 已知代价（面试可以主动说）：并列值会被**先后顺序**拆到相邻两档，所以严格意义上不同值可能同分；
    本项目现存数据里走 `rfm_table` 时结果与行序无关（`groupby` 默认按 key 排序），这点已实测确认。
- F 用**交易笔数**而不是**购买件数**：频次衡量的是"来店次数"，一次买 100 件和分 100 次买
  在客户价值上是两回事。
- F 默认用等频而不是固定阈值：实测同一套固定阈值 `1 / 2-3 / 4+` 在近 3 个月窗口上的分布是
  `{1: 296, 2: 98, 3: 4}`（74% 挤在最低档，F 维度几乎失去区分度），近一年窗口才均衡
  `{1: 292, 2: 357, 3: 163}`。根因是短窗口人均只有 1.34 笔，"来 4 次以上"本来就极罕见。
- 三项得分拼接为 RFM 组合码（如 `333`），共 27 种组合。

### 从 27 种组合到 8 个分群

27 种组合没法一一配运营动作（运营只关心"这个人群值不值得投、该投什么"），所以再折一层：
**每一维的 3 档折成「高 / 低」两档 —— 3 = 高，1/2 = 低**，2×2×2 正好落回经典的 8 分群。
折档规则在 `utils/rfm.py` 的 `band_of()` / `segment_key()` / `segment_of()`，分群定义（名字 / 徽章色 / 运营动作）
集中在 `SEGMENTS` 字典里；页面「分群明细与运营动作」面板和表格最后一列都由它驱动，模板里不写业务判断。

| 分群 | R×F×M | 运营动作 |
|---|---|---|
| 重要价值客户 | 高·高·高 | 保持：VIP 服务 + 专属权益，重点维护防流失 |
| 重要唤回客户 | 低·高·高 | 唤回：专属召回券 + 客服回访，这类人丢了最可惜 |
| 重要深耕客户 | 高·低·高 | 提频次：关联品类推荐 + 复购提醒 |
| 重要挽留客户 | 低·低·高 | 最高优先级挽回：大额券 / 一对一沟通 |
| 一般价值客户 | 高·高·低 | 提客单：满减 / 套装搭配，把高频低客单往上带 |
| 一般保持客户 | 低·高·低 | 唤醒 + 提客单：低成本触达，试探性优惠 |
| 一般发展客户 | 高·低·低 | 培育：新客券 + 首购引导，先让他买第二次 |
| 一般挽留客户 | 低·低·低 | 低成本维持：短信触达即可，不投广告费 |

- **为什么只有最高档算"高"**：分档本身就是相对排名（前 1/3 / 中 1/3 / 后 1/3），折两档时取"最高档 = 高"最简单也最好解释；
  23 种剩下 11 种"中档"不会再单独造分群，否则 27 种又回来了。
- 表里同时保留 `rfm_group`（如 `213`）与 `segment`（中文分群名）：前者是原始三位码，方便下钻和排查；
  后者是给运营看的结论。面试时先讲 27 种组合的细粒度，再讲"为什么运营只需要 8 类"。


---

## 七、接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/` | 跳转客户列表 | 登录 |
| GET/POST | `/login` | 登录页 / 提交登录（表单字段 `mobile` / `pwd` / `csrf_token`） | 匿名 |
| POST | `/logout` | 退出登录 | 登录 |
| GET | `/customer/list` | 客户列表（LEFT JOIN 账号状态） | **员工 / 管理员** |
| GET/POST | `/customer/update` | 修改客户地区 | **员工 / 管理员** |
| POST | `/customer/delete` | 软注销：`cus_status=2` + 停用关联账号 | **管理员** |
| GET | `/order/list` | 订单列表（客户只看自己的；员工 / 管理员看全部） | 登录 |
| GET/POST | `/order/create` | 客户下单（金额按 DB 单价计算；一次性表单 token 防重复提交） | **客户** |
| GET/POST | `/order/update` | 修改数量（仅 `order_status=1`；客户限本人） | 登录 + 归属校验 |
| POST | `/order/delete` | 取消订单（`1 → 3`，复用取消状态机） | 登录 + 归属校验 |
| GET | `/product/list` | 商品列表（客户只看在售） | 登录 |
| GET/POST | `/product/add` | 新增商品（`product_id` 自动生成 `SKU-` 前缀） | **员工 / 管理员** |
| GET/POST | `/product/update` | 修改商品名称 / 分类 / 单价 / 上下架 | **员工 / 管理员** |
| POST | `/product/delete` | 下架（`product_status=2`，历史订单保留） | **员工 / 管理员** |
| GET/POST | `/register` | 客户自助注册（`customers` + `users(role=0)` 双表事务） | 匿名 |
| GET / POST | `/login` / `/logout` | 登录 / 登出 | 匿名 / 登录 |
| GET | `/ge_rfm?window=` | RFM 分析结果页（`window` 取 `3m` / `6m` / `12m` / `all`） | **员工 / 管理员** |
| POST | `/clear_input` | 清空并重新导入交易数据（检测到业务数据会 409 拒绝） | **管理员** |
| GET | `/pay/order/<order_id>` | 查询订单支付状态（`payable` 表示是否可支付） | 登录 + 归属校验 |
| POST | `/pay/prepay/<order_id>` | 发起支付：锁 + 幂等 + 状态校验是真的，**网关未接 → 固定 501** | 登录 + 幂等 + 归属校验 |
| POST | `/pay/cancel/<order_id>` | 取消未支付订单（`1 → 3`，条件 `UPDATE`） | 登录 + 幂等 + 归属校验 |
| POST | `/pay/confirm/<order_id>` | 员工确认线下收款（`1 → 2`，写 `paid_at` / `paid_by`） | **员工 / 管理员** |

写方法（POST / PUT / PATCH / DELETE）统一在 `before_app_request` 里校验 CSRF：
浏览器表单走 `csrf_token` 字段，接口调用可放 `X-CSRF-Token` 头；`/pay/*` 三个接口还需带
`X-Request-Id`（幂等键），其余 HTML 表单接口用 CSRF token + 一次性 `form_token` 即可。

---

## 八、测试与验证

`pytest` 共 **98 条**，按文件分：

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/test_rfm.py` | 15 | 观察窗口切分、先切窗口再分箱、R 反向打分、并列值 / 小样本、组合码、三档折两档与 8 分群汇总 |
| `tests/test_rfm_report.py` | 11 | 页面聚合层：KPI / 分群行 / 直方图分桶合计 / 切点与分档一致 / 环比与「全部」无上一周期 / 人民币折算 / 明细行 |
| `tests/test_quality.py` | 11 | 必填列、重复交易号、金额不一致、异常值阈值、报告结构 |
| `tests/test_import_db.py` | 18 | 临时库建表、事务原子性、外键约束、重复导入、表结构契约、路由与安全（登录跳转 / 管理员校验 / CSRF） |
| `tests/test_lock.py` | 7 | 互斥、token 校验、TTL 续期、放锁、锁忙异常、条件 `UPDATE` 的 `rowcount` |
| `tests/test_idempotent.py` | 7 | 2xx 写缓存 / 重放、非 JSON 不缓存、状态码保留、缓存损坏降级、旧格式兼容、缺 request_id |
| `tests/test_p2_crud.py` | 29 | P2 下单闭环：客户注册 / 下单 / 改数量 / 取消、三档角色权限、订单归属（防 IDOR）、软删除与注销恢复、员工确认收款、导入守卫、CSRF 与表单参数校验 |

另外做过 9 项针对**真实 Redis** 的手工验证（锁互斥、token 不匹配不误删、`PTTL` 续期、
TTL 到期自动失效、`SCRIPT FLUSH` 后 `EVALSHA` 自动回退重载、8 线程并发临界区最多 1 人），
结论与脚本见 [`docs/P1-支付与Redis锁.md`](docs/P1-支付与Redis锁.md) 第 12 节。

---

## 九、已知限制与后续计划

按优先级：

- [x] **7 个 CRUD 空壳**：已落地（2026-10-20）—— 7 个空壳接口全部实现，删除类操作改为软删除
      （`cus_status` / `product_status` / `order_status`），订单操作带归属校验；客户注册 / 下单 /
      员工确认收款闭环打通；全套 98 条测试全绿。代码手册见 [`docs/P2-实施手册-00-索引.md`](docs/P2-实施手册-00-索引.md)。
- [ ] **退款流程**：已支付订单当前冻结、无退款 —— 属于 P1-B 明确不做的范围，见 P1 文档第 11 节；
      真要做需要状态机加「退款中 / 已退款」并写流水表，属于另一个完整功能。
- [x] **客户分层命名**：已落地 —— 三维各 3 档折成高/低两档（3 = 高），2×2×2 落回经典 8 分群
      （重要价值 / 重要唤回 / 重要深耕 / 重要挽留 / 一般价值 / 一般保持 / 一般发展 / 一般挽留），
      页面带「分群明细与运营动作」面板，表格每行带分群徽章。
- [x] **统一异常处理**：已落地 —— 403 / 404 / 405 / 500 统一渲染中文错误页（`templates/error_t.html`），
      500 记录服务端日志；权限拦截与越权访问不再返回纯文本。
- [ ] **多格式接入**：`static/archive/` 下的另一种 CSV 结构（30000 行版本）暂不支持，
      计划用「新增一个 loader 而不改已有代码」的方式扩展。
- [ ] **物化 RFM 结果表 + 后端分页**：当前每次访问实时计算，数据量上去后需要预计算。
      （列表页分页已抽成 `static/js/list.js` 组件，但仍是前端伪分页、整表一次渲染，
      订单多时首屏 HTML 会偏大，后续换成后端 `LIMIT/OFFSET`。）
- [x] **前端资源自建**：已去掉 Bootstrap / jQuery，改为自建设计系统（`static/css/app.css`
      + `static/js/*.js`，零第三方前端依赖、零构建步骤），页面风格统一为深色顶栏 + 轻卡片。
- [x] **支付模块止于骨架**：真实网关 / 回调验签 / 支付流水表 / 退款（P1-B）**已决定不做**，
      接口契约与并发控制保留，见 `docs/P1-支付与Redis锁.md` 第 11 节。

---

## 十、项目文档

| 文档 | 内容 |
|---|---|
| [`docs/01-改造路线图.md`](docs/01-改造路线图.md) | 每个改造点的**原因、方案取舍、面试讲法**，以及 RFM 口径是怎么从"按年"演进到"观察窗口"的 |
| [`docs/02-面试题库.md`](docs/02-面试题库.md) | 结合本项目的面试问答准备（项目类 + 八股 + HR） |
| [`docs/P0-2-登录权限与CSRF.md`](docs/P0-2-登录权限与CSRF.md) | 登录 / 会话 / 权限分级 / CSRF 的实现与踩坑记录 |
| [`docs/P1-支付与Redis锁.md`](docs/P1-支付与Redis锁.md) | 锁 / 幂等 / 状态机的完整实现、端到端时序、生产注意与**面试口径**（首页有一页速查图） |
| [`docs/P2-实施手册-00-索引.md`](docs/P2-实施手册-00-索引.md) | P2 全套实施手册：01 总览与库表 / 02 认证与订单核心 / 03 页面路由与导入保护 / 04 模板 / 05 测试与面试口径，附 `P2-migrate.sql` |
| [`docs/00-使用说明.md`](docs/00-使用说明.md) | 本地环境与改造步骤说明 |
