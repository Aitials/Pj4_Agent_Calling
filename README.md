# Pj4 Agent Calling — AI 外呼获客助手

基于 LangChain 的 Agent 骨架：飞书机器人提供客户查询、消息推送与任务指派；一知 AICC 负责外呼与通话回收；高意向通话经 AI 分析后自动派单给销售，并同步到飞书卡片与任务简报。客户名单来自企查查导出。

## 架构

两条链路共用 MySQL 业务库：

```
【对话侧】飞书长连接 → app/router（thread_id = 会话标识）
               → BaseAgent（create_agent）
                    ├─ 短期记忆：PostgreSQL checkpoint（LangGraph AsyncPostgresSaver）
                    ├─ 上下文裁剪：最近 10 轮 + 6000 token 上限
                    └─ 工具：客户查询 / 飞书消息 / 飞书任务

【外呼侧】run_sync 轮询一知 → ingest 入库（call_record）
               ├─ 阻断闸门：空号 / 拒接 / 黑名单 / 勿扰 → 不派单
               ├─ 高意向标签 → dispatch_notice + lead_brief
               ├─ run_analyzer：LLM 结构化分析 → 裁决（撤销 / 保留 / 补派）
               └─ run_notifier：飞书任务 + 高意向卡 → AI 分析卡 → 简报附件
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
| 一知外呼任务对接 | ✅ 完成 | 登录态自动续期（AES 加密密码 + 失效重登）、线路查询、任务创建/启动、状态查询 |
| 任务运行概况查询 | ✅ 完成 | `query_aicc_task_status`：语义匹配任务 → 筛选卡 → 任务列表卡 → 概况卡（状态/进度/通话效果/意向分布/异常提示），Redis 缓存 60 秒 |
| 通话与转写回收 | ✅ 完成（轮询） | `run_sync` 定时增量拉取，去重、失败日志、多进程互斥；回调通道代码就绪，公网入口受证书限制 |
| AI 意向分析 | ✅ 完成 | 9 字段结构化输出（等级/理由/原话/需求/异议/话术），可合并历史通话 |
| 高意向派单 | ✅ 完成 | 标签触发 → 阻断闸门 → 轮转销售 → 飞书任务；AI 可撤销/补派并记录 `level_consistent` |
| 客户分析简报 | ✅ 完成 | Markdown 简报本地留档 + 上传飞书任务附件 |
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

### 外呼链路常驻进程

下面四个进程按需启动；都支持 `--once` 只跑一轮，调试时优先用 `--once`。

```powershell
# 轮询一知通话记录 → 入库 → 派单（默认 60 秒一轮）
.venv\Scripts\python.exe scripts\run_sync.py --interval 60

# AI 分析 + 派单裁决（默认 10 秒扫描一次）
.venv\Scripts\python.exe scripts\run_analyzer.py --interval 10

# 飞书任务 / 卡片 / 简报附件派发（默认 5 秒扫描一次）
.venv\Scripts\python.exe scripts\run_notifier.py --interval 5

# 一知回调接收（可选；需要公网 HTTPS 入口，未接通时靠轮询兜底）
.venv\Scripts\python.exe scripts\run_callback.py
```

单轮演练：

```powershell
.venv\Scripts\python.exe scripts\run_sync.py --once
.venv\Scripts\python.exe scripts\run_analyzer.py --once --batch 5
.venv\Scripts\python.exe scripts\run_notifier.py --once
# 只读拉取（不写库）：
.venv\Scripts\python.exe scripts\sync_call_records.py --job <任务ID> --days 7 --dry-run
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

# ===== 一知 AICC（外呼链路必填）=====
AICC_PHONE=
AICC_PASSWORD=
AICC_COOKIE=                    # 首次可留空，登录成功后自动回写

# ===== 飞书（启动 run_feishu.py 才需要）=====
FEISHU_APP_ID=
FEISHU_APP_SECRET=
```

可选配置：`HISTORY_MAX_ROUNDS`（默认 10）、`HISTORY_MAX_TOKENS`（默认 6000）、`PG_POOL_MAX`（默认 5）、`ALLOW_GROUP_CHAT`（默认 false）、`AICC_DASHBOARD_TTL`（默认 600）、`AICC_TASK_STATUS_TTL`（默认 60）。

Redis 与一知回调均可暂不配置：Redis 不可用时缓存自动降级（不影响拉取），回调相关变量只在启动 `run_callback.py` 时必填。完整清单见 `.env.example`。

## 短期记忆实现

- **存储**：LangGraph `AsyncPostgresSaver`，checkpoint 表由 `saver.setup()` 自动创建（`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`），落在 `PG_SCHEMA` 指定的 schema 下。
- **会话标识**：`thread_id` = 发送人 `open_id`（群聊已关闭，见 `ALLOW_GROUP_CHAT`）。
- **读写时机**：每轮只把本轮 user 消息交给 Agent，历史由图状态自动加载；回复过程中 checkpoint 自动落盘，无需手工写库。
- **上下文裁剪**：`app/agents/base_agent.py` 的 `_TrimHistoryMiddleware` 先保留最近 `HISTORY_MAX_ROUNDS` 轮（以 `HumanMessage` 为边界，避免拆散 tool call），再用 `HISTORY_MAX_TOKENS` 兜底。**只裁剪送给模型的内容，checkpoint 仍保留完整对话**。
- **并发控制**：同一 `thread_id` 在 `app/router` 内串行处理，避免并发写 checkpoint 互相覆盖。
- **清理会话**：按线程删除，例如 `await saver.adelete_thread(thread_id)`。

## 目录结构

```
├── app/
│   ├── agents/                   # base_agent（对话主 Agent）+ customer_agent（分析子 Agent）
│   ├── callback/                 # 一知回调接收（FastAPI + 签名校验 + AES 解密 + 解析）
│   ├── cards/                    # 飞书卡片：任务 / 高意向 / AI 分析结果
│   ├── channels/                 # 飞书长连接通道
│   ├── clients/                  # 外部平台客户端：tanyibot / aicc_call_record / feishu_api
│   ├── core/                     # 配置、缓存、枚举、表单、文本对齐
│   ├── flows/                    # 多步交互流程（任务创建等）
│   ├── repositories/             # 数据访问：客户、通话、派单、分析、简报、同步游标
│   ├── router/                   # 消息路由（飞书 → Agent）
│   ├── schemas/                  # Pydantic 数据模型
│   ├── services/                 # 业务编排：同步、入库、意向判定、AI 分析、裁决、简报
│   └── tools/                    # LangChain @tool 层
├── scripts/                      # 常驻进程与运维脚本
│   ├── run_sync.py               # 轮询一知 → 入库 → 派单
│   ├── run_analyzer.py           # AI 分析 + 派单裁决
│   ├── run_notifier.py           # 飞书任务 / 卡片 / 简报附件派发
│   ├── run_callback.py           # 一知回调服务（可选）
│   ├── sync_call_records.py      # 手工补拉指定任务（支持 --dry-run）
│   ├── sync_intent_rules.py      # 同步一知意向标签规则
│   ├── sync_feishu_users.py      # 同步飞书成员
│   ├── selftest_callback.py      # 回调链路自检
│   ├── live_check_callback.py    # 公网回调连通性检查
│   ├── db_scan.py                # 扫业务库看有啥表
│   ├── export_asr.py             # 导出 ASR 转写
│   ├── feishu_api_check.py       # 飞书接口连通性检查
│   └── seed_test_customers.py    # 造测试数据
├── sql/                          # 建表与迁移脚本（000_init_db.py 初始化 + 001~ 迁移）
├── state/                        # 运行时状态：日志 / 简报 / 转写（gitignore）
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
| Python | 3.14.7 | venv |
| LangChain | 1.5.0 | create_agent + middleware |
| langgraph-checkpoint-postgres | 3.2.1 | 对话 checkpoint |
| psycopg | 3.3.9 | PostgreSQL 驱动（异步连接池） |
| pymysql | 1.2.4 | MySQL 驱动 |
| lark-oapi | 1.7.5 | 飞书长连接 |
| redis | 8.1.0 | 看板/任务状态缓存（不可用自动降级） |
| fastapi / uvicorn | 0.163.2 / 0.54.0 | 一知回调接收 |
| httpx | 0.28.1 | 一知接口调用 |

> Windows 注意：psycopg 异步连接池不支持默认的 `ProactorEventLoop`，`run_feishu.py` 已显式改用 `asyncio.SelectorEventLoop`。

## 文档索引

设计与接口文档统一放在 `AI messages/`：

| 文档 | 内容 |
|---|---|
| `外呼需求.md` | 原始需求 |
| `开发计划书_一周版.md` | 一周开发计划（D1~D7） |
| `一知智能AICC开放平台接口文档（官方）.md` | 一知官方接口文档（合并版） |
| `一知智能AICC接口文档.md` | 一知接口实测清单 |
| `业务表结构说明.md` | 业务表结构与一知增量同步设计 |
| `外呼高意向通知实现方案_v2.md` | 派单/通知/附件方案（当前实现依据） |
| `企查查导出字段适配与数据库调整方案_V1.1.md` | 名单导入字段映射 |
| `内网映射回调接入方案（交接）.md` | 回调公网接入现状（阻塞说明） |
| `销售跟进话术模板.md` | 分析子 Agent 的话术约束来源 |

> `AI messages/` 含内部资料，已在 `.gitignore` 中忽略，克隆仓库后不随代码分发。

## 安全约定

- `.env`、`state/`、`AI messages/`、`.idea/` 均已 `.gitignore`
- 数据库密码、飞书密钥只写 `.env`，不提交仓库

## 下一步（待做）

1. 真实名单导入验证：企查查 Excel 入库后，确认通话号码能反查到 `credit_code`（否则不派单）
2. 录音本地留档 / 对象存储转存（当前只保存供应商 URL，存在过期风险）
3. 打通回调公网入口（证书问题），让实时回调替代/补充轮询
4. 一知任务号落库到 `outbound_job`，补齐任务↔供应商映射与对账
5. 统计看板（接通率 / 意向分布 / 时效）与跟进闭环状态机（二期）
6. D7 演示剧本与端到端彩排（含边界样例）
