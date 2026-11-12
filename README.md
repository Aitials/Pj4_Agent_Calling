# Pj4 Agent Calling — AI 外呼获客助手

基于 LangChain 的 Agent 骨架，对接好客搜（whxt.jshks.com）客户管理后台，自动同步客户档案、通话记录，并通过飞书机器人对外提供查询能力。

## 功能清单

| 能力 | 实现状态 | 说明 |
|---|---|---|
| 好客搜自动登录 + Cookie 缓存 | ✅ 完成 | ddddocr OCR 识别验证码，2 小时 TTL，过期自动重登 |
| 客户档案全量导出 | ✅ 完成 | `@tool export_haokesou_customers`，支持分页全量拉取 |
| 客户档案按姓名/手机号查询 | ✅ 完成 | `@tool query_customer_profile`，查本地 MySQL |
| 飞书机器人接入 | ✅ 完成 | 长连接 + 流式卡片回复 |
| 通话历史增量同步 | 📋 设计中 | 接口已探明，表结构已设计，待建表实现 |
| 通话 ASR 转写 | 📋 设计中 | 依赖 `member/backdetail` 接口，待确认是否存储 |
| 状态流转历史追踪 | 📋 设计中 | 可选表 `customer_status_history`，待决策 |

## 快速开始

```powershell
# 1. 克隆 & 创建虚拟环境
git clone git@github.com:Aitials/Pj4_Agent_Calling.git
cd Pj4_Agent_Calling
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. 复制模板并填写真实值（.env 已 gitignore）
copy .env.example .env

# 3. 初始化数据库
# 用 MySQL 客户端连上业务库后执行：
#   sql/001_create_customer_profile.sql

# 4. 终端单轮测试
.venv\Scripts\python.exe main.py "帮我导出好客搜的客户资料"

# 5. 启动飞书长连接
.venv\Scripts\python.exe run_feishu.py
```

## `.env` 必填项

```ini
# ===== 模型服务（OpenAI 兼容）=====
LLM_API_KEY=                    # 必填，默认走 glm-4.5-air

# ===== 好客搜登录（自动刷新 Cookie 依赖）=====
HAOKESOU_USERNAME=              # 必填，填了就走 OCR 自动登录
HAOKESOU_PASSWORD=              # 必填

# ===== MySQL =====
MYSQL_PASSWORD=                 # 必填
MYSQL_DATABASE=                 # 必填

# ===== 飞书（启动 run_feishu.py 才需要）=====
FEISHU_APP_ID=
FEISHU_APP_SECRET=
```

## 目录结构

```
├── app/                          # 业务代码
│   ├── agents/                   # LLM Agent 封装（create_agent + tools）
│   │   └── base_agent.py
│   ├── channels/                 # 飞书长连接通道
│   │   ├── base.py
│   │   └── feishu.py
│   ├── clients/                  # 好客搜 HTTP 客户端层
│   │   ├── haokesou.py           # 客户列表接口 + Cookie 失效兜底
│   │   └── haokesou_auth.py      # OCR 自动登录 + Cookie JSON 缓存（7200秒）
│   ├── core/                     # 基础层：配置加载、文本对齐工具
│   ├── repositories/             # 数据访问层（只读查询，按 phone upsert）
│   │   └── customer_repo.py
│   ├── router/                   # 消息路由（飞书 → Agent）
│   ├── schemas/                  # Pydantic 数据模型
│   ├── tools/                    # LangChain @tool 层（对外能力）
│   │   ├── haokesou_customers.py #   export_haokesou_customers
│   │   └── customer_query.py     #   query_customer_profile
├── scripts/                      # 一次性脚本（迁移、测试、对比）
│   ├── export_customers.py       # 客户列表导出（CLI）
│   ├── db_scan.py                # 扫业务库看有啥表
│   ├── seed_test_customers.py    # 造测试数据（按 phone upsert）
│   └── compare_schema.py         # DB vs 好客搜接口字段对比
├── sql/                          # 建表 SQL
│   └── 001_create_customer_profile.sql
├── state/                        # 运行时状态（gitignore）
│   └── .haokesou_cookie          # Cookie JSON 缓存
├── 客户档案表设计.md              # 主表字段说明
├── 从表与增量同步设计.md          # 通话历史表 + 同步方案
├── 接口说明.md                    # 好客搜接口清单（带验证状态标记）
├── 外呼需求.md                    # 原始需求文档
├── .env.example                   # 配置模板
├── main.py                        # 终端单轮入口
├── run_feishu.py                  # 飞书长连接入口
└── requirements.txt
```

## 好客搜接口已验证清单

| 接口 | 状态 | 备注 |
|---|---|---|
| `POST /user/Member/ajax_intentional_member` | ✅ 已实装 | 客户列表（支持分页、手机号/姓名/任务/时间/意愿等级筛选） |
| `POST /user/index/login` | ✅ 已实装 | OCR 自动登录（ddddocr） |
| `GET /user/index/verify` | ✅ 已实装 | 验证码图片 |
| `POST /user/callrecord/get_new_call_historical_record` | 📋 已探明 | 历史通话记录（按日分表，支持 `start_call_time` 增量） |
| `POST /user/member/backdetail` | 📋 已探明 | 通话详情 + ASR 转写 + 录音 URL |
| `POST /user/Plan/ajax_task_statistics` | 📋 已探明 | 任务统计 |

## Cookie 机制

```
ensure_cookie()
  │
  ├─ 环境变量 HAOKESOU_COOKIE 存在？ ──是──▶ 直接返回（忽略过期）
  │
  ├─ 缓存文件 expires_at > now？ ──是──▶ 直接返回（0 开销）
  │
  └─ 走 OCR 登录刷新一次（POST /user/index/login）
     └─ 覆写 state/.haokesou_cookie 为 JSON {cookie, expires_at}
```

TTL 默认 7200 秒（2 小时），改 `COOKIE_TTL_SEC` 一行即可调整。

接口请求层额外兜底：好客搜返回非 JSON（Cookie 失效）→ 强制重登一次 → 重试原请求。

## Agent 工具

| 工具名 | 入参 | 返回 |
|---|---|---|
| `export_haokesou_customers` | `limit=50, max_pages=200` | 终端表格文本（对齐打印） |
| `query_customer_profile` | `keyword, limit=10` | 手机号精确 / 姓名模糊，1 条出详情、多条出摘要表、0 条给提示 |

## 技术栈

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | 3.14 | venv |
| LangChain | 1.5.0 | create_agent + LCEL |
| httpx | — | HTTP 客户端（Cookie 登录、好客搜接口） |
| pymysql | — | MySQL 驱动 |
| ddddocr | — | 验证码 OCR（show_ad=False） |
| lark-oapi | 1.7.5 | 飞书长连接 |

## 安全约定

- `.env`、`state/`、`.idea/` 均已 `.gitignore`
- 好客搜账号密码写 `.env`，不提交仓库
- Cookie 文件是 JSON，`state/` 已忽略

## 下一步（待做）

1. 执行 ALTER TABLE 补主表缺失列（`source_status` / `source_member_id` / `is_look`）
2. 建通话历史表 `call_record`（唯一键 `record_id`，增量拉取）
3. 写通话历史同步脚本（先拉列表再补转写 / 按需调 `backdetail`）
4. 字段映射规范化（`compay_name → company_name`、`sex 字符串→TINYINT`、`Unix秒→DATETIME`）
