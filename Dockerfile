# 单镜像跑 4 个常驻进程（飞书通道 / 同步 / 分析 / 通知），命令由 compose 指定
# Python 版本与开发环境一致；若某个依赖在 3.14 下没有 linux wheel，
# 可用 `docker compose build --build-arg PYTHON_VERSION=3.13` 回退
ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai

# tzdata 让 TZ=Asia/Shanghai 生效（简报、通话时间都用 datetime.now()）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，代码改动不会重复下载
COPY requirements.txt ./
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.txt

COPY . .

RUN mkdir -p /app/state /app/state/briefs /app/state/logs

CMD ["python", "run_feishu.py"]
