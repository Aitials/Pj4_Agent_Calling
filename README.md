# Pj4 Agent Calling — AI 外呼获客助手

基于 LangChain 的 Agent 骨架，通过飞书机器人对外提供客户查询、消息推送与任务指派能力。外呼与通话数据源规划为一知 AICC（测试账号对接中），客户名单来自企查查导出。

## 功能清单

| 能力 | 实现状态 | 说明 |
|---|---|---|
| 客户档案按姓名/手机号查询 | ✅ 完成 | `@tool query_customer_profile`，查本地 MySQL |
| 飞书机器人接入 | ✅ 完成 | 长连接 + 流式卡片回复 |
| 飞书主动消息与任务 | ✅ 完成 | `find_feishu_member` / `send_feishu_message` / `create_feishu_task` |
| 一知外呼任务对接 | 📋 开发中 | 线路查询、任务创建、启动、状态同步 |
| 通话与转写回收 | 📋 待条件 | 一知测试账号暂无真实通话与转写 |
| AI 意向分析 | 📋 设计完成 | 结构化输出意向等级、理由、原文引用 |
| 企查查名单导入 | 📋 待模板 | 按统一社会信用代码归并企业 |
| 销售对话短期记忆 | 📋 待开发 | 按销售维度保留最近 10 轮对话 |
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

# 3. 初始化数据库
# 用 MySQL 客户端连上业务库后执行：
#   sql/001_create_customer_profile.sql

# 4. 终端单轮测试
.venv\Scripts\python.exe main.py "查一下客户 13800001111 的档案"

# 5. 启动飞书长连接
.venv\Scripts\python.exe run_feishu.py
```

## `.env` 必填项

```ini
# ===== 模型服务（OpenAI 兼容）=====
LLM_API_KEY=                    # 必填，默认走 glm-4.5-air

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
│   ├── clients/                  # 外部平台客户端层（一知客户端待接入）
│   ├── core/                     # 基础层：配置加载、文本对齐工具
│   ├── repositories/             # 数据访问层（按 phone 查询与 upsert）
│   │   └── customer_repo.py
│   ├── router/                   # 消息路由（飞书 → Agent）
│   ├── schemas/                  # Pydantic 数据模型
│   ├── tools/                    # LangChain @tool 层（对外能力）
│   │   ├── customer_query.py     #   query_customer_profile
│   │   ├── feishu_message.py     #   find_feishu_member / send_feishu_message
│   │   └── feishu_task.py        #   create_feishu_task
├── scripts/                      # 辅助脚本
│   ├── db_scan.py                # 扫业务库看有啥表
│   ├── seed_test_customers.py    # 造测试数据
│   ├── sync_feishu_users.py      # 同步飞书成员
│   └── feishu_api_check.py       # 飞书接口连通性检查
├── sql/                          # 建表 SQL
│   └── 001_create_customer_profile.sql
├── state/                        # 运行时状态（gitignore）
├── main.py                       # 终端单轮入口
├── run_feishu.py                 # 飞书长连接入口
└── requirements.txt
```

## Agent 工具

| 工具名 | 入参 | 返回 |
|---|---|---|
| `query_customer_profile` | `keyword, limit=10` | 手机号精确 / 姓名模糊，1 条出详情、多条出摘要表、0 条给提示 |
| `find_feishu_member` | `name` | 按姓名匹配飞书成员，返回 open_id |
| `send_feishu_message` | `open_id, content` | 主动发送文本或卡片消息 |
| `create_feishu_task` | `open_id, summary, ...` | 创建并指派飞书任务 |

## 技术栈

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | 3.14 | venv |
| LangChain | 1.5.0 | create_agent + LCEL |
| pymysql | — | MySQL 驱动 |
| lark-oapi | 1.7.5 | 飞书长连接 |

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

- `.env`、`state/`、`.idea/` 均已 `.gitignore`
- 数据库密码、飞书密钥只写 `.env`，不提交仓库

## 下一步（待做）

1. D1：建业务表（`credit_code` 为企业主键）+ 销售对话短期记忆
2. D2：打通一知任务创建、启动与状态查询
3. D3：飞书通知销售（先用供应商意向标签触发）
4. D4：AI 分析接管通知规则
5. D5：通话与转写回收（待真实通话产出后回归）
6. D6：企查查名单导入与清洗
7. D7：联调与演示
