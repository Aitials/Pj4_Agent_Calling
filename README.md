# Pj4 Agent Calling — AI 外呼获客助手

基于 LangChain 的 Agent 骨架，通过飞书机器人对外提供客户查询、消息推送与任务指派能力。外呼与通话数据源规划为一知 AICC（测试账号对接中），客户名单来自企查查导出。

## 架构

```
飞书长连接 → app/router（thread_id = 会话标识）
               → BaseAgent（create_agent）
                    ├─ 短期记忆：PostgreSQL checkpoint（LangGraph AsyncPostgresSaver）
                    ├─ 上下文裁剪：最近 10 轮 + 6000 token 上限
                    └─ 工具：客户查询 / 飞书消息 / 飞书任务
               → MySQL：客户档案、外呼任务、通话、AI 分析、导入批次等业务表
```

两套库分工明确：**MySQL 存业务数据，PostgreSQL 只存对话 checkpoint**，两者无外键、无跨库 join。

## 功能清单

| 能力 | 实现状态 | 说明 |
|---|---|---|
| 客户档案按姓名/手机号查询 | ✅ 完成 | `@tool query_customer_profile`，查本地 MySQL |
| 飞书机器人接入 | ✅ 完成 | 长连接 + 流式卡片回复 |
| 飞书主动消息与任务 | ✅ 完成 | `find_feishu_member` / `send_feishu_message` / `create_feishu_task` |
| 销售对话短期记忆 | ✅ 完成 | PG checkpoint 按销售（open_id）持久化，重启不丢；最近 10 轮 + token 上限裁剪 |
| 群聊支持 | ❌ 已关闭 | 只处理单聊，群消息直接忽略；`ALLOW_GROUP_CHAT=true` 可恢复 |
| 企查查名单导入 | ✅ 脚本可用 | `sql/012_import_qichacha.py`，按统一社会信用代码归并企业 |
| 一知外呼任务对接 | 📋 开发中 | 线路查询、任务创建、启动、状态同步 |
| 通话与转写回收 | 📋 待条件 | 一知测试账号暂无真实通话与转写 |
| AI 意向分析 | 📋 设计完成 | 结构化输出意向等级、理由、原文引用 |
| 好客搜客户导出 | ❌ 已下线 | 2026-11-16 删除相关代码，改由一知承接 |

## 快速开始

```powershell
# 1. 克隆 & 创建虚拟环境
git clone git@github.com:Aitials/Pj4_Agent_Calling.git
cd Pj4_Agent_Calling
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. 复制模板并填写真实值（.env 已 gitignore）
copy .env.example .env

# 3. 初始化 MySQL 业务库（自动建库、建表、插入测试数据）
.venv\Scripts\python.exe sql\000_init_db.py
# 需要重建时加 --recreate（旧表直接删除，数据不保留）
.venv\Scripts\python.exe sql\000_init_db.py --recreate

# 4. 初始化 PostgreSQL 记忆库（只需建空库，表由程序启动时自动创建）
psql -U postgres -c "CREATE DATABASE agent_calling_memory;"

# 5. 终端单轮测试（无记忆，仅验证模型与工具链路）
.venv\Scripts\python.exe main.py "查一下客户 13800001111 的档案"

# 6. 启动飞书长连接（带完整短期记忆）
.venv\Scripts\python.exe run_feishu.py
```

## `.env` 必填项

```ini
# ===== 模型服务（OpenAI 兼容）=====
LLM_API_KEY=                    # 必填，默认走 glm-4.5-air

# ===== MySQL（业务数据）=====
MYSQL_PASSWORD=                 # 必填
MYSQL_DATABASE=                 # 必填，如 agent_calling

# ===== PostgreSQL（对话 checkpoint）=====
PG_PASSWORD=                    # 必填
PG_DATABASE=agent_calling_memory
PG_SCHEMA=memory

# ===== 飞书（启动 run_feishu.py 才需要）=====
FEISHU_APP_ID=
FEISHU_APP_SECRET=
```

可选配置：`HISTORY_MAX_ROUNDS`（默认 10）、`HISTORY_MAX_TOKENS`（默认 6000）、`PG_POOL_MAX`（默认 5）、`ALLOW_GROUP_CHAT`（默认 false）。

## 短期记忆实现

- **存储**：LangGraph `AsyncPostgresSaver`，checkpoint 表由 `saver.setup()` 自动创建（`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`），落在 `PG_SCHEMA` 指定的 schema 下。
- **会话标识**：`thread_id` = 发送人 `open_id`（群聊已关闭，见 `ALLOW_GROUP_CHAT`）。
- **读写时机**：每轮只把本轮 user 消息交给 Agent，历史由图状态自动加载；回复过程中 checkpoint 自动落盘，无需手工写库。
- **上下文裁剪**：`app/agents/base_agent.py` 的 `_TrimHistoryMiddleware` 先保留最近 `HISTORY_MAX_ROUNDS` 轮（以 `HumanMessage` 为边界，避免拆散 tool call），再用 `HISTORY_MAX_TOKENS` 兜底。**只裁剪送给模型的内容，checkpoint 仍保留完整对话**。
- **并发控制**：同一 `thread_id` 在 `app/router` 内串行处理，避免并发写 checkpoint 互相覆盖。
- **清理会话**：按线程删除，例如 `await saver.adelete_thread(thread_id)`。

## 目录结构

```
├── app/                          # 业务代码
│   ├── agents/
│   │   └── base_agent.py         # Agent 封装 + 上下文裁剪中间件
│   ├── channels/                 # 飞书长连接通道
│   │   ├── base.py
│   │   └── feishu.py
│   ├── clients/                  # 外部平台客户端层（一知客户端待接入）
│   ├── core/                     # 配置加载、文本对齐工具
│   ├── repositories/
│   │   ├── customer_repo.py      # MySQL 客户档案查询
│   │   ├── feishu_user_repo.py   # 飞书成员
│   │   └── memory.py             # PostgreSQL checkpoint 连接池
│   ├── router/                   # 消息路由（飞书 → Agent）
│   ├── schemas/                  # Pydantic 数据模型
│   └── tools/                    # LangChain @tool 层
│       ├── customer_query.py     #   query_customer_profile
│       ├── feishu_message.py     #   find_feishu_member / send_feishu_message
│       └── feishu_task.py        #   create_feishu_task
├── scripts/                      # 辅助脚本
│   ├── db_scan.py                # 扫业务库看有啥表
│   ├── seed_test_customers.py    # 造测试数据
│   ├── sync_feishu_users.py      # 同步飞书成员
│   └── feishu_api_check.py       # 飞书接口连通性检查
├── sql/                          # 数据库脚本
│   ├── 000_init_db.py            # 业务库初始化（建表 + 测试数据）
│   ├── 001~008、010              # 业务表建表脚本
│   └── 012_import_qichacha.py    # 企查查 Excel 清洗入库（交互式输入路径）
├── state/                        # 运行时状态（gitignore）
├── main.py                       # 终端单轮入口（无记忆）
├── run_feishu.py                 # 飞书长连接入口（带记忆）
└── requirements.txt
```

## Agent 工具

| 工具名 | 入参 | 返回 |
|---|---|---|
| `query_customer_profile` | `keyword, limit=10` | 企业名称/法人模糊、号码精确；1 条出详情、多条出摘要、0 条给提示 |
| `find_feishu_member` | `name` | 按姓名匹配飞书成员，返回 open_id |
| `send_feishu_message` | `open_id, content` | 主动发送文本或卡片消息 |
| `create_feishu_task` | `open_id, summary, ...` | 创建并指派飞书任务 |

## 技术栈

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | 3.14 | venv |
| LangChain | 1.5.0 | create_agent + middleware |
| langgraph-checkpoint-postgres | 3.2.1 | 对话 checkpoint |
| psycopg | 3.3.9 | PostgreSQL 驱动（异步连接池） |
| pymysql | — | MySQL 驱动 |
| lark-oapi | 1.7.5 | 飞书长连接 |

> Windows 注意：psycopg 异步连接池不支持默认的 `ProactorEventLoop`，`run_feishu.py` 已显式改用 `asyncio.SelectorEventLoop`。

## 文档索引

设计与接口文档统一放在 `AI messages/`：

| 文档 | 内容 |
|---|---|
| `外呼需求.md` | 原始需求 |
| `接口说明.md` | 好客搜接口清单与实现方法（已归档，代码已删除） |
| `一知智能AICC接口文档.md` | 一知接口实测清单 |
| `业务表结构说明.md` | 业务表结构与一知增量同步设计 |
| `开发计划书_一周版.md` | 一周开发计划 |

## 安全约定

- `.env`、`state/`、`AI messages/`、`.idea/` 均已 `.gitignore`
- 数据库密码、飞书密钥只写 `.env`，不提交仓库

## 下一步（待做）

1. 待接口数据导入落地后，再补两个只读工具：查客户通话历史、查待通知清单
2. D2：打通一知任务创建、启动与状态查询
3. D3：飞书通知销售（先用供应商意向标签触发）
4. D4：AI 分析接管通知规则
5. D5：通话与转写回收（待真实通话产出后回归）
6. D6：企查查名单导入与清洗
7. D7：联调与演示
