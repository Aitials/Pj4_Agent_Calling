# 部署手册 · Docker Compose 一键部署

目标机：`yctz@192.168.1.9`（Ubuntu 26.04，x86_64）
项目目录（U 盘，只读不写）：`/run/media/yctz/9DADF5B233857B3E/project/Pj4_Agent_Calling`
运行目录（必须在本地 ext4）：`~/project/Pj4_Agent_Calling`

---

## 架构一览

```
mysql:8.0           ← 业务库（customer_profile / call_record / dispatch_notice / lead_brief ...）
postgres:16        ← Agent 短期记忆 checkpoint（langgraph AsyncPostgresSaver）
redis:7-alpine      ← 看板数据缓存（不可用时应用自动降级）
app (单镜像)        ← 同时起 4 个常驻进程：
  ├─ feishu         ← 飞书长连接通道
  ├─ sync           ← 60s 轮询一知 AICC → 通话入库 → 派单
  ├─ analyzer       ← 10s 扫 lead_brief → LLM 分析 → 裁决
  └─ notifier       ← 5s 扫 dispatch_notice → 飞书任务 + 卡片 + 简报附件
```

镜像：`python:3.14-slim`（失败可退 `3.13`，见下文）。

---

## 前置条件（已确认均 OK）

| 组件 | 版本 | 状态 |
|---|---|---|
| Docker | 29.10.0 | ✅ 已装，daemon 已启动 |
| Docker Compose | v5.8.0 | ✅ 随 Docker 提供 |
| 国内镜像源 | `1ms.run` + `xuanyuan.me` | ✅ `/etc/docker/daemon.json` 已配置 |
| git | 2.53.0 | ✅ |
| 存储 | /dev/nvme0n1p2 233G（已用 13G） | ✅ 空间充足 |
| 内存 | 7G（可用 3.9G） | ✅ |

### 验证命令

```bash
docker --version          # 期望：Docker version 29.10.0
docker compose version   # 期望：Docker Compose version v5.8.0
docker ps                # 期望：空列表（daemon 正常）
cat /etc/docker/daemon.json
# 期望看到两行 registry-mirrors
```

### 如果 daemon.json 有脏数据（比如行末残留 `eof`）

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me"
  ]
}
EOF
sudo systemctl reset-failed docker
sudo systemctl restart docker
sleep 3 && docker ps   # 验证
```

---

## Step 1 · 复制项目到本地 ext4 目录

**不要在 U 盘路径上跑 compose！** ntfs-3g 挂载不支持 Docker bind mount 和文件锁。

```bash
mkdir -p ~/project
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.env.swp' \
  '/run/media/yctz/9DADF5B233857B3E/project/Pj4_Agent_Calling/' \
  ~/project/Pj4_Agent_Calling/

cd ~/project/Pj4_Agent_Calling
ls -la
# 应看到：Dockerfile、docker-compose.yml、requirements.txt、app/、scripts/、sql/、env、.env.example ...
```

---

## Step 2 · 准备 .env

配置文件已在 U 盘里，文件名是 `env`（没有点）：

```bash
cp env .env
rm -f .env.swp   # 清 vim 临时文件
```

### 必查项（grep 一下确认）

```bash
grep -E '^(MYSQL_DATABASE|MYSQL_PASSWORD|PG_PASSWORD|AICC_PHONE|AICC_PASSWORD|LLM_API_KEY|FEISHU_APP_ID|FEISHU_APP_SECRET|MYSQL_HOST|PG_HOST|REDIS_HOST)=' .env
```

硬性要求：

| 变量 | 要求 |
|---|---|
| `MYSQL_DATABASE` | 必须是 `agent_calling`（SQL 里写死了 `USE agent_calling`） |
| `MYSQL_HOST` | `.env` 里写 `127.0.0.1` 即可，compose 的 `environment` 会覆盖成 `mysql` |
| `PG_HOST` | 同上，compose 覆盖成 `postgres` |
| `REDIS_HOST` | 同上，compose 覆盖成 `redis` |
| `AICC_COOKIE` | 已有，失效会自动用 `AICC_PHONE/PASSWORD` 重登并回写到 `/app/.env` |

### 验证 compose 能正确插值

```bash
docker compose config -q && echo "config OK" || echo "config 有问题"
docker compose config | grep -E "(MYSQL_HOST|PG_HOST|REDIS_HOST)" | head -3
# 应看到 MYSQL_HOST: mysql / PG_HOST: postgres / REDIS_HOST: redis
```

---

## Step 3 · 构建镜像

```bash
cd ~/project/Pj4_Agent_Calling
docker compose build
```

### 如果 pip 报找不到 linux wheel（某个包太新没有 3.14 轮子）

```bash
PYTHON_VERSION=3.13 docker compose build
# Dockerfile 里已支持这个 build-arg
```

### 如果 pip 下载慢/超时

```bash
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
docker compose build
```

构建完成后应看到：`Image agent-calling:latest Built`

---

## Step 4 · 起中间件

```bash
docker compose up -d mysql postgres redis
```

等待健康检查通过（首次 MySQL 初始化约 30 秒）：

```bash
sleep 25
docker compose ps
# 期望：mysql / postgres / redis 三个服务都是 Up (healthy)
```

### 如果 mysql 长时间 Starting 没进 Healthy

```bash
docker compose logs mysql   # 看 MySQL 在报什么
docker compose ps           # 确认状态
```

最常见：`.env` 里 `MYSQL_PASSWORD` 没填，导致 root 用户创建失败。

---

## Step 5 · 初始化业务库

```bash
docker compose run --rm init-db
```

期望输出（末尾类似这样）：

```
=== 初始化业务库 agent_calling（MySQL 8.x.x） ===
[1/12] 001_create_customer_profile.sql → 表 customer_profile 建表完成
...
=== 已跳过测试数据（--no-seed） ===
=== 完成：业务库 agent_calling 共 12 张表 ===
```

### 必查表

```bash
docker compose exec mysql mysql -uroot -p"$MYSQL_PASSWORD" agent_calling -e "SHOW TABLES;"
# 应看到：customer_profile, customer_contact, import_batch, outbound_job,
#          sync_log, call_record, agent_analysis, dispatch_notice,
#          intent_level_rule, sales_assignee, dispatch_assignee_cursor, lead_brief
```

### 如果 init-db 报错要重来

```bash
# 删中间件数据卷 + 重建（会清空业务数据，仅第一次部署用）
docker compose down -v mysql
docker compose up -d mysql
sleep 25 && docker compose run --rm init-db
```

---

## Step 6 · 起 4 个业务容器

```bash
docker compose up -d feishu sync analyzer notifier
docker compose ps
# 期望：7 个容器全部 Up（mysql/postgres/redis + feishu/sync/analyzer/notifier）
```

### 如果 feishu 启动后立刻退出

```bash
docker compose logs feishu | tail -30
# 常见原因：.env 里缺 FEISHU_APP_ID / PG_PASSWORD / LLM_API_KEY
```

---

## Step 7 · 验收

### 7.1 飞书通道是否正常

```bash
docker compose logs -f feishu
# 看是否有长连接建立成功的日志
# Ctrl+C 退出
```

### 7.2 各 worker 单轮跑通

```bash
# 通话同步（如有真实一知任务会拉到数据；无任务则输出 0 条）
docker compose run --rm sync python scripts/run_sync.py --once

# AI 分析
docker compose run --rm analyzer python scripts/run_analyzer.py --once

# 通知派发
docker compose run --rm notifier python scripts/run_notifier.py --once
```

### 7.3 PostgreSQL checkpoint 表

```bash
docker compose exec postgres psql -U postgres -d agent_calling_memory -c "\dn"
# 应看到 memory schema
docker compose exec postgres psql -U postgres -d agent_calling_memory -c "SET search_path TO memory,public; \dt"
# 应看到 checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations
```

### 7.4 Redis 自动降级验证

```bash
docker compose exec redis redis-cli ping
# 应返回 PONG
# 手动停掉 redis 后 feishu 不会崩，看板查询会降级失败（不影响主流程）
```

---

## 常用运维命令

```bash
# 容器状态
docker compose ps

# 看日志（最后 200 行 + 跟随）
docker compose logs -f --tail 200 feishu
docker compose logs -f sync analyzer notifier mysql

# 重启某个服务
docker compose restart feishu

# 进入容器 shell 调试
docker compose exec feishu bash
docker compose exec mysql mysql -uroot -p"$MYSQL_PASSWORD" agent_calling

# 全部停
docker compose stop

# 全部停 + 删除容器（数据卷保留）
docker compose down

# 全部停 + 删除容器 + **删除数据卷**（慎用，会清空 MySQL/PG/Redis 全部数据）
docker compose down -v

# 重建某个服务（配置改了之后）
docker compose up -d --build feishu

# 清理无用镜像/容器/网络（磁盘满时用）
docker system prune -a
```

---

## 数据备份

数据全部在 Docker 命名卷里：

```bash
docker volume ls
# 应看到 docker-agent-calling_mysql_data / pg_data / redis_data / app_state
```

### 备份 MySQL

```bash
docker compose exec mysql mysqldump -uroot -p"$MYSQL_PASSWORD" agent_calling > backup_$(date +%Y%m%d).sql
```

### 备份 PostgreSQL

```bash
docker compose exec postgres pg_dump -U postgres agent_calling_memory > pg_backup_$(date +%Y%m%d).sql
```

### 备份整个卷（冷备份）

```bash
# 停中间件
docker compose stop mysql postgres redis

# 导出
mkdir -p ~/backup
for vol in mysql_data pg_data redis_data app_state; do
  docker run --rm -v "docker-agent-calling_${vol}:/data" \
    -v ~/backup:/backup alpine tar czf "/backup/${vol}_$(date +%Y%m%d).tar.gz" -C /data .
done

# 恢复（示例，谨慎）
docker run --rm -v "docker-agent-calling_mysql_data:/data" \
  -v ~/backup:/backup alpine sh -c "cd /data && tar xzf /backup/mysql_data_YYYYMMDD.tar.gz"

docker compose start mysql postgres redis
```

---

## 常见问题排查速查

| 现象 | 可能原因 | 排查/解决 |
|---|---|---|
| `docker compose build` pip 找不到 wheel | Python 3.14 太新 | `PYTHON_VERSION=3.13 docker compose build` |
| `docker compose pull` 很慢 | 镜像源挂了 | `cat /etc/docker/daemon.json` 换其他源 |
| mysql 长时间 Starting | MYSQL_PASSWORD 未填 | `.env` 里补密码，`docker compose down -v mysql && docker compose up -d mysql` |
| init-db 报 MySQL 连不上 | host/port 不对 | compose 里 MYSQL_HOST=mysql，不要改；MYSQL_PASSWORD 要对 |
| init-db 报 SQL 里的表不存在 | MYSQL_DATABASE 不是 `agent_calling` | `.env` 里强制改成 `agent_calling` |
| init-db 报 `Access denied` | root 密码不匹配 | 同上，down -v 后重来 |
| feishu 启动立刻退出 | 缺 FEISHU_APP_ID / PG_PASSWORD / LLM_API_KEY | `docker compose logs feishu` 看具体 |
| 所有 worker 都不产出数据 | 没一知任务 / 没企查查名单 | 正常现象，有真实任务才会跑 |
| VS Code Remote-SSH 空白 | 本项目 Docker 无关 | 见底部「附：VS Code Server 硬重置」 |
| Docker daemon 启动失败 | daemon.json 有脏 JSON | 按 Step 前置条件里的修法重装 |

---

## 附：VS Code Remote-SSH 硬重置（本项目无关，只是本机问题处理参考）

```bash
# 服务器上
rm -rf ~/.vscode-server /run/user/1000/vscode-* /tmp/vscode-*
pkill -9 -f vscode-server 2>/dev/null

# Windows 上关闭所有 VS Code 窗口后重开，Ctrl+Shift+P：
#   Remote-SSH: Kill VS Code Server on Host...
#   Remote-SSH: Uninstall VS Code Server from Host...
#   Developer: Reload Window
# 再 Connect to Host
```

---

## 附：compose 服务速查

| 服务 | 镜像 | 命令 | 说明 |
|---|---|---|---|
| mysql | mysql:8.0 | — | 业务库，utf8mb4 + Asia/Shanghai |
| postgres | postgres:16 | — | 记忆库，`POSTGRES_DB=agent_calling_memory` |
| redis | redis:7-alpine | — | 看板缓存，有密码则自动 `requirepass` |
| init-db | agent-calling:latest | `python sql/000_init_db.py --no-seed` | 一次性，profile=init |
| feishu | agent-calling:latest | `python run_feishu.py` | 飞书长连接通道 |
| sync | agent-calling:latest | `python scripts/run_sync.py --interval 60` | 轮询一知 AICC |
| analyzer | agent-calling:latest | `python scripts/run_analyzer.py --interval 10` | AI 分析 Worker |
| notifier | agent-calling:latest | `python scripts/run_notifier.py --interval 5` | 飞书任务/卡片/简报派发 |

业务容器都挂载：`./.env:/app/.env`（读写）、`app_state:/app/state`（日志/简报/媒体）。
无宿主端口暴露（飞书长连接是出站 WebSocket，无回调）。

---

文档生成：2026-11-20
项目根目录：`~/project/Pj4_Agent_Calling`
