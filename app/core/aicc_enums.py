"""一知 AICC 官方枚举与本地映射。

依据：`AI messages/一知智能AICC开放平台接口文档（官方）.md`
- 任务状态：第 6 节「任务状态枚举」
- 通话结果：第 6 节「通话结果枚举」

说明：
- `PAUSED` / `STOPPED` / `IN_PROGRESS` 是后台接口出现过的历史值，官方枚举中没有，
  这里保留兼容，避免旧任务被误判为未知状态。
- `RUNNABLE` 官方定义为「可运行」，既可出现在运行概况里，也允许发起启动，
  因此同时归入 RUNNING 与 STARTABLE；真正「已在跑」的判断用 ACTIVE。
"""

from __future__ import annotations

TASK_STATUS_LABELS = {
    "NOT_STARTED": "未开始",
    "NOT_GENERATED": "未生成",
    "RUNNABLE": "可运行",
    "IN_QUEUE": "排队中",
    "IN_PROCESS": "进行中",
    "IN_PROGRESS": "进行中",
    "USER_PAUSE": "用户暂停",
    "PAUSED": "已暂停",
    "STOPPED": "已停止",
    "SYSTEM_SUSPENDED": "系统暂停",
    "SYSTEM_HANG_UP": "系统挂起",
    "MAINTAIN": "系统维护",
    "ACCOUNT_DISABLE": "账户禁用",
    "WAITING_FOR_REDIAL": "等待重呼",
    "EXPIRED": "任务超时",
    "COMPLETED": "已完成",
    "TERMINATE": "已终止",
    "ARCHIVED": "已归档",
}

# 官方「启动任务」接口说明：可开始的任务状态为 未开始 / 用户暂停 / 系统挂起 / 账号禁用 / 系统维护；
# PAUSED、STOPPED 为后台历史别名，保留兼容
STARTABLE_TASK_STATUSES = {
    "NOT_STARTED",
    "RUNNABLE",
    "USER_PAUSE",
    "SYSTEM_HANG_UP",
    "ACCOUNT_DISABLE",
    "MAINTAIN",
    "PAUSED",
    "STOPPED",
}

# 真正占用并发、无需再次启动的状态
ACTIVE_TASK_STATUSES = {"IN_PROCESS", "IN_PROGRESS", "IN_QUEUE"}

# 运行概况里需要按「运行中」展示的状态
RUNNING_TASK_STATUSES = ACTIVE_TASK_STATUSES | {"RUNNABLE"}

PAUSED_TASK_STATUSES = {
    "USER_PAUSE",
    "SYSTEM_SUSPENDED",
    "SYSTEM_HANG_UP",
    "MAINTAIN",
    "WAITING_FOR_REDIAL",
}

ENDED_TASK_STATUSES = {"COMPLETED", "TERMINATE", "ARCHIVED", "EXPIRED"}


# 官方通话结果枚举
CALL_RESULT_LABELS = {
    "ANSWERED": "已接听",
    "NO_ANSWER": "无应答",
    "BUSY": "忙线中",
    "POWER_OFF": "关机",
    "OUT_OF_SERVICE": "停机",
    "REFUSED": "拒接",
    "VACANT_NUMBER": "空号",
    "CAN_NOT_CONNECT": "无法接通",
    "EXTENSION_NUMBER": "分机号错误",
    "CALL_LIMIT": "呼叫受限",
    "FROM_PHONE_ERROR": "主叫欠费",
    "SYSTEM_ERROR": "外呼失败",
    "CALL_LOSS": "多并发呼损",
    "TRANSFER_ARTIFICIAL": "转人工呼损",
    "LINE_INTERCEPT": "线路拦截",
}

# 一知 resultStatus -> 本地 call_status
CALL_STATUS_MAP = {
    "ANSWERED": "ANSWERED",
    "NO_ANSWER": "NO_ANSWER",
    "BUSY": "NO_ANSWER",
    "VACANT_NUMBER": "INVALID",
    "OUT_OF_SERVICE": "INVALID",
    "EXTENSION_NUMBER": "INVALID",
    "REFUSED": "REJECTED",
    "POWER_OFF": "FAILED",
    "CAN_NOT_CONNECT": "FAILED",
    "CALL_LIMIT": "FAILED",
    "FROM_PHONE_ERROR": "FAILED",
    "SYSTEM_ERROR": "FAILED",
    "CALL_LOSS": "FAILED",
    "TRANSFER_ARTIFICIAL": "FAILED",
    "LINE_INTERCEPT": "FAILED",
}

CALL_STATUS_VALUES = ("ANSWERED", "NO_ANSWER", "INVALID", "REJECTED", "FAILED", "UNKNOWN")


def normalize_call_status(raw: str) -> str:
    """把一知 resultStatus 归一为本地 call_status；未知值保留 UNKNOWN，原文另存 status_raw。"""
    return CALL_STATUS_MAP.get((raw or "").strip().upper(), "UNKNOWN")
