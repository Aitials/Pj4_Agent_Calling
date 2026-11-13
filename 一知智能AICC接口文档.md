# 一知智能 AICC 工作台 接口文档

> 探测时间：2026-11-13
> 平台：一知智能 AICC 工作台（杭州一知智能科技有限公司）
> 域名：`https://aicc.tanyibot.com`
> 测试租户：8369（张存义-测试）

---

## 一、概述

### 1.1 基础信息

| 项目 | 值 |
|---|---|
| 接口前缀 | `/apiEngine/`、`/apiPlatform/`、`/customerDataPlatform/`、`/scrm/` |
| 鉴权方式 | Cookie 中的 `token` 字段（base64 编码的 64 位 hex session token） |
| 请求头 | `X-Requested-With: XMLHttpRequest`（部分接口必需） |
| 数据格式 | JSON（`Content-Type: application/json`） |
| 响应格式 | `{ code, data, resultMsg, requestId, host, errorStackTrace }` |
| 成功标识 | `code: 200` |

### 1.2 鉴权说明

- 登录成功后，服务端设置 Cookie：`token=<base64_token>; refresh_token=<base64_token>; SERVERID=<lb_session>`
- 所有接口请求需携带 Cookie（`credentials: 'include'`）
- token 解码后为 64 位十六进制字符串（疑似 session token 的 SHA-256）

### 1.3 实时通信

平台使用 **Socket.IO（Engine.IO v3）** 实现实时数据推送，存在两条独立通道：

| 通道 | 路径 | 用途 |
|---|---|---|
| 全局 AICC | `/socket.io/?token=<token>` | 外呼任务、坐席等全局实时事件 |
| 会员中心专用 | `/scrmio/?token=<token>` | 会员中心业务事件推送 |

**连接参数：**
- 协议：初始 polling，支持升级为 WebSocket
- 鉴权：URL query 参数 `token`（与 Cookie 中 token 一致）
- 心跳：`pingInterval=25000ms`，`pingTimeout=60000ms`

**会员中心实时推送事件：**

| 事件名 | 说明 |
|---|---|
| `importSuccEvent` | 批量导入客户完成后实时推送结果 |
| `syncTagSucc` | 标签同步完成推送 |
| `loginSuccEvent` | 登录成功事件 |

---

## 二、会员中心（SCRM）接口

入口页面：`https://aicc.tanyibot.com/active-scrm-admin/customerManagement`

### 2.1 客户管理

#### 2.1.1 分页查询客户列表

- **接口**：`POST /customerDataPlatform/account/pageAccount`
- **说明**：查询租户下的客户列表，支持分页和筛选
- **请求体**：
  ```json
  {
    "pageNum": 1,
    "pageSize": 10
  }
  ```
- **响应**（`code: 200`）：
  ```json
  {
    "code": 200,
    "message": "成功",
    "status": "SUCCESS",
    "data": {
      "totalElements": 1265285,
      "pages": 421762,
      "number": 1,
      "pageSize": 10,
      "content": [
        {
          "accountId": 11294250576,
          "tenantId": 8369,
          "name": null,
          "mainPhoneNumber": "18010856899",
          "gender": "MALE",
          "location": "安徽合肥",
          "areaCode": "0551",
          "birthday": null,
          "accountCreateTime": "2026-10-28 17:18:17",
          "channelName": "EXCEL_IMPORT",
          "channels": ["EXCEL_IMPORT"],
          "tagList": null,
          "callCount": null,
          "receiveCount": null,
          "intentLevelName": "",
          "belongUserName": "销售部",
          "wechatBuddy": false
        }
      ]
    }
  }
  ```

#### 2.1.2 新建客户

- **接口**：`POST /customerDataPlatform/account/createAccount`

#### 2.1.3 获取客户详情

- **接口**：`POST /customerDataPlatform/account/getAccountInfo`

#### 2.1.4 更新客户信息

- **接口**：`POST /customerDataPlatform/account/updateAccountInfo`

#### 2.1.5 删除客户

- **接口**：`POST /customerDataPlatform/account/deleteAccount`

#### 2.1.6 批量导入客户

- **接口**：`POST /customerDataPlatform/account/importAccount`

#### 2.1.7 批量打标签

- **接口**：`POST /customerDataPlatform/account/batchAddTag`

#### 2.1.8 批量推送到 AI 外呼

- **接口**：`POST /customerDataPlatform/account/batchAddAccountToAicc`

#### 2.1.9 批量加入黑名单

- **接口**：`POST /customerDataPlatform/account/batchAddAccountToBlackList`

#### 2.1.10 查询订单信息

- **接口**：`POST /customerDataPlatform/account/queryOrderInfo`

#### 2.1.11 获取客户属性定义

- **接口**：`POST /customerDataPlatform/account/getCustomerAttribute`

#### 2.1.12 获取导入模板

- **接口**：`GET /customerDataPlatform/account/getImportAccountTemplate`

### 2.2 客户导出（异步批量任务）

#### 2.2.1 发起导出

- **接口**：`POST /customerDataPlatform/account/exportAccount`
- **说明**：导出是异步批量任务，返回任务 ID 后需轮询状态
- **请求体**：与 `pageAccount` 查询条件一致
  ```json
  {
    "pageNum": 1,
    "pageSize": 10
  }
  ```
- **响应**：
  ```json
  {
    "code": 200,
    "data": {
      "springBatchJobLogId": 6513418,
      "jobInstanceId": 6159836
    }
  }
  ```

#### 2.2.2 查询导出任务状态

- **接口**：`POST /scrm/springBatchJob/getBatchJobStatus`
- **请求体**：
  ```json
  {
    "springBatchJobLogId": 6513418
  }
  ```
- **响应**：
  ```json
  {
    "code": 200,
    "data": {
      "completedCount": 0,
      "executingCount": 0
    }
  }
  ```

#### 2.2.3 查询任务实例列表

- **接口**：`POST /scrm/springBatchJob/jobInstances`
- **请求体**：`{ "pageNum": 1, "pageSize": 10 }`

### 2.3 标签管理

| 接口 | 方法 | 说明 |
|---|---|---|
| `/customerDataPlatform/tag/listTagCategory` | POST | 标签分类列表 |
| `/customerDataPlatform/tag/listTagCategoryWithGroup` | POST | 标签分类及分组 |
| `/customerDataPlatform/tag/createTagCategory` | POST | 创建标签分类 |
| `/customerDataPlatform/tag/updateTagCategoryName` | POST | 更新分类名称 |
| `/customerDataPlatform/tag/deleteTagCategoryById` | POST | 删除分类 |
| `/customerDataPlatform/tag/createTagGroupAndTag` | POST | 创建标签分组及标签 |
| `/customerDataPlatform/tag/updateTagGroupAndTag` | POST | 更新标签分组及标签 |
| `/customerDataPlatform/tag/deleteTagGroupById` | POST | 删除标签分组 |
| `/customerDataPlatform/tag/getTagInfoByGroupId` | POST | 按分组获取标签 |
| `/customerDataPlatform/tag/exportTag` | POST | 导出标签 |
| `/customerDataPlatform/tag/createImportTagJob` | POST | 创建标签导入任务 |
| `/customerDataPlatform/tag/validateTagDuplication` | POST | 校验标签重复 |

### 2.4 人群管理

| 接口 | 方法 | 说明 |
|---|---|---|
| `/customerDataPlatform/crowd/getCrowdInfoList` | POST | 人群列表 |
| `/customerDataPlatform/crowd/getCrowdDetail` | POST | 人群详情 |
| `/customerDataPlatform/crowd/queryCrowdDetail` | POST | 查询人群明细 |
| `/customerDataPlatform/crowd/editCrowd` | POST | 编辑人群 |
| `/customerDataPlatform/crowd/deleteCrowd` | POST | 删除人群 |
| `/customerDataPlatform/crowd/pushCrowd` | POST | 推送人群 |
| `/customerDataPlatform/crowd/getPushProgress` | POST | 获取推送进度 |
| `/customerDataPlatform/crowd/getCrowdPushState` | POST | 获取人群推送状态 |
| `/customerDataPlatform/crowd/beginCompute` | POST | 开始计算人群 |
| `/customerDataPlatform/crowd/calculatePushSize` | POST | 计算推送数量 |
| `/customerDataPlatform/crowd/mergeCrowd` | POST | 合并人群 |
| `/customerDataPlatform/crowd/breakUpCrowd` | POST | 拆分人群 |
| `/customerDataPlatform/crowd/ruleCreateCrowd` | POST | 规则创建人群 |
| `/customerDataPlatform/crowd/exportCrowd` | POST | 导出人群 |

### 2.5 客户字段定义

- **接口**：`POST /customerDataPlatform/customerHeader/getList`
- **说明**：返回客户列表的表头/自定义字段定义（非客户数据）

### 2.6 客户生命周期

- **接口**：`GET /scrm/customerLife/selectCustomerLifeByTenantId?newVersion=false`

### 2.7 地址信息

- **接口**：`GET /customerDataPlatform/address/getProvince`

---

## 三、AI 外呼任务接口

### 3.1 任务管理

#### 3.1.1 创建任务

- **接口**：`POST /apiEngine/robotCallJob/create`
- **Controller**：`RobotCallJobController.createRobotCallJob`
- **请求体**（`RobotCallJobCreateVO`）：
  ```json
  {
    "concurrencyQuota": 1,
    "jobPhoneNumberIdList": [21871],
    "robotCallJob": {
      "name": "任务名称",
      "dialogFlowId": 66286,
      "phoneType": "LANDLINE",
      "mode": "MANUAL",
      "robotCount": 1,
      "dailyStartTime": "00:00:00",
      "dailyEndTime": "23:59:00",
      "daysOfWeek": ["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],
      "onWorkingDays": false,
      "csStaffGroupId": 0,
      "transferPhoneChooseType": "POLLING",
      "csDistributionMethod": "FREE",
      "concurrencyQuota": 1,
      "priority": 100,
      "robotCallJobType": "COMMON"
    }
  }
  ```
- **必填字段校验**：
  - `concurrencyQuota`：线路并发数不能为空
  - `jobPhoneNumberIdList`：主叫号码不能为空
  - `robotCallJob`：robotCallJob 对象不能为空

#### 3.1.2 修改任务

- **接口**：`POST /apiEngine/robotCallJob/modify`

#### 3.1.3 复制任务

- **接口**：`POST /apiEngine/robotCallJob/copy`

#### 3.1.4 删除任务

- **接口**：`POST /apiEngine/robotCallJob/delete`

#### 3.1.5 执行/启动任务

- **接口**：`POST /apiEngine/robotCallJob/execute`

#### 3.1.6 批量执行任务

- **接口**：`POST /apiEngine/robotCallJob/executeJobs`

#### 3.1.7 任务列表

- **接口**：`POST /apiEngine/robotCallJob/list`

#### 3.1.8 任务详情

- **接口**：`GET /apiEngine/robotCallJob/getById?robotCallJobId=<id>`

#### 3.1.9 检查任务名

- **接口**：`POST /apiEngine/robotCallJob/checkName`

#### 3.1.10 简单信息列表

- **接口**：`GET /apiEngine/robotCallJob/getSimpleInfoList`

#### 3.1.11 任务状态

- **接口**：`GET /apiEngine/robotCallJob/getStatus?robotCallJobIds=<id1>,<id2>&systemType=CALL_OUT`

#### 3.1.12 任务统计

- **接口**：`POST /apiEngine/robotCallJob/getJobStatsInfoList`

#### 3.1.13 获取任务自定义属性

- **接口**：`GET /apiEngine/robotCallJobProperties/getAll?robotCallJobId=<id>`

#### 3.1.14 任务字段列表

- **接口**：`POST /apiEngine/robotCallJobField/listField`

### 3.2 任务对象（robotCallJob）关键字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | String | 任务名称 |
| `dialogFlowId` | Long | BOT（话术）ID |
| `phoneType` | String | 外呼方式：`LANDLINE`（线路）/ `MOBILE`（手机） |
| `mode` | String | 启动方式：`MANUAL`（手动）/ `AUTO`（自动） |
| `robotCount` | Integer | BOT 数量 |
| `concurrencyQuota` | Integer | 线路并发数 |
| `dailyStartTime` | String | 每日开始时间，如 `00:00:00` |
| `dailyEndTime` | String | 每日结束时间，如 `23:59:00` |
| `daysOfWeek` | Array | 拨打星期，如 `["MONDAY",...]` |
| `onWorkingDays` | Boolean | 是否仅法定工作日 |
| `priority` | Integer | 优先级 |
| `status` | String | 状态：`NOT_STARTED`/`IN_PROGRESS`/`COMPLETED`/`PAUSED`/`ARCHIVED` 等 |
| `csStaffGroupId` | Long | 人工坐席组 ID（转人工配置） |
| `transferPhoneChooseType` | String | 转接电话选择方式：`POLLING`（轮询） |
| `transferTenantPhoneNumberId` | Long | 转接电话号码 ID |
| `csDistributionMethod` | String | 客服分配方式：`FREE`（空闲分配） |
| `redialTimes` | Integer | 重拨次数 |
| `redialInterval` | Integer | 重拨间隔（秒） |
| `customerWhiteGroupIds` | Array | 客户白名单分组 ID 列表 |

### 3.3 任务文件夹

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiEngine/robotCallJobFolder/treeFolder` | POST | 文件夹树 |
| `/apiEngine/robotCallJobFolder/add` | POST | 新建文件夹 |
| `/apiEngine/robotCallJobFolder/update` | POST | 更新文件夹 |
| `/apiEngine/robotCallJobFolder/delete` | POST | 删除文件夹 |
| `/apiEngine/robotCallJobFolder/dragSort` | POST | 拖拽排序 |

---

## 四、外呼线路（电话号码）接口

### 4.1 获取线路列表

- **接口**：`GET /apiEngine/tenantPhoneNumber/list`
- **响应**：
  ```json
  {
    "code": 200,
    "data": [
      {
        "tenantPhoneNumberId": 20433,
        "tenantId": 8369,
        "phoneType": "LANDLINE",
        "phoneNumber": "（测试）萧山SIP自切换专线-电信0571-82658280",
        "phoneShowName": null,
        "phoneUseType": "MOBILE",
        "supportCallOut": true,
        "supportCallIn": false,
        "supportCsSeat": true,
        "concurrenceLimit": 0,
        "enabledStatus": 0,
        "stopStatus": true,
        "overdue": false,
        "healthIndex": 100.0
      }
    ]
  }
  ```

### 4.2 获取已激活线路

- **接口**：`GET /apiEngine/tenantPhoneNumber/getActive`
- **说明**：按 `phoneType` 分组返回已激活的线路

### 4.3 获取所有线路

- **接口**：`GET /apiEngine/tenantPhoneNumber/listAll`

### 4.4 线路字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `tenantPhoneNumberId` | Long | 线路 ID（创建任务时填入 `jobPhoneNumberIdList`） |
| `phoneType` | String | 线路类型：`LANDLINE`（固话）/ `MOBILE`（手机） |
| `phoneNumber` | String | 显示号码 |
| `phoneShowName` | String | **来电显示名称**（可配置为公司名） |
| `supportCallOut` | Boolean | 是否支持外呼 |
| `supportCallIn` | Boolean | 是否支持呼入 |
| `supportCsSeat` | Boolean | **是否支持人工坐席** |
| `concurrenceLimit` | Integer | 并发上限 |
| `enabledStatus` | Integer | 启用状态：1=启用，0=停用 |
| `healthIndex` | Float | 线路健康指数 |

---

## 五、人工坐席与通话转接接口

### 5.1 坐席管理

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiEngine/csRobot/getStaffStatus` | GET | 获取坐席状态 |
| `/apiEngine/csRobot/setCsStatus` | POST | 设置坐席状态 |
| `/apiEngine/csRobot/getCsStaffInfo` | GET | 获取坐席信息 |
| `/apiEngine/csRobot/csAccountList` | POST | 坐席账号列表 |
| `/apiEngine/csRobot/getGroupByType` | POST | 按类型获取分组 |
| `/apiEngine/csRobot/staffGroupList` | POST | 坐席分组列表 |
| `/apiEngine/csRobot/callJobStaffGroupList` | POST | 关联任务的坐席分组 |

### 5.2 通话转接

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiEngine/csCallWaiting/getCsCallWaitingList` | POST | 获取等待接听列表 |
| `/apiEngine/csCallWaiting/csWaitingCallTransfer` | POST | **等待通话转接（转人工）** |
| `/apiEngine/csCallWaiting/callHistoryImportToWaitingCall` | POST | 通话历史导入等待接听 |
| `/apiEngine/csCallWaiting/importToWaitingCallFromCallRecord` | POST | 从通话记录导入等待接听 |

### 5.3 外呼前线路检查

- **接口**：`POST /apiEngine/csRecord/checkLineLimitBeforeCallOut`
- **说明**：外呼前检查线路并发限制

### 5.4 转人工配置（任务级别）

任务对象中的转人工相关字段：

| 字段 | 说明 |
|---|---|
| `csStaffGroupId` | 人工坐席组 ID，指定转接目标组 |
| `transferPhoneChooseType` | 转接电话选择方式：`POLLING`（轮询） |
| `transferTenantPhoneNumberId` | 转接使用的电话号码 ID |
| `csDistributionMethod` | 客服分配方式：`FREE`（空闲优先） |

> 在线路配置中，`supportCsSeat: true` 表示该线路支持人工坐席通话。

---

## 六、通话记录接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiEngine/callRecord/getInfoList` | POST | 通话记录列表 |
| `/apiEngine/callRecord/getInfoListCount` | POST | 通话记录数 |
| `/apiEngine/callRecord/getInfoListFromJob` | POST | 按任务查通话记录 |
| `/apiEngine/callRecord/getInfoListFromJobCount` | POST | 按任务查记录数 |
| `/apiEngine/callRecord/callDetailList` | POST | 通话明细列表 |
| `/apiEngine/callRecord/markIntentLevel` | POST | 标记意向等级 |
| `/customerDataPlatform/callRecord/getCallRecordList` | POST | 会员中心通话记录 |
| `/customerDataPlatform/callRecord/getCallRecordDetail` | POST | 通话记录详情 |

---

## 七、统计接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiEngine/callStats/callJobFullStatsInfo` | GET | 任务完整统计 |
| `/apiEngine/callStats/callStatsInfo` | POST | 通话统计 |
| `/apiEngine/callStats/businessAnalysisInfo` | POST | 业务分析 |
| `/apiEngine/callStats/exportCallStatsInfo` | POST | 导出通话统计 |
| `/apiEngine/callStatsIndex/indexStatsInfo` | POST | 首页统计 |
| `/apiEngine/callJobMonitor/count` | GET | 任务监控计数 |

---

## 八、用户与租户接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/apiPlatform/user/getUserInfo` | GET | 获取当前用户信息 |
| `/apiPlatform/user/listAll` | GET | 用户列表 |
| `/apiPlatform/user/update` | POST | 更新用户 |
| `/apiEngine/tenant/getIntentInfo` | GET | 租户意向信息 |
| `/apiEngine/groupTenant/isGroupManager` | GET | 是否集团管理员 |

---

## 九、关键问题答复

### 9.1 能否换成自己的固话？

**可以。** 线路类型 `phoneType` 支持 `LANDLINE`（固话）。创建任务时通过 `jobPhoneNumberIdList` 指定要使用的固话线路 ID 即可。

### 9.2 来电显示能否更换公司名字？

**可以配置。** 线路对象中存在 `phoneShowName` 字段，用于设置来电显示名称。当前测试账号该字段为 `null`，可通过线路配置接口设置为公司名称。

### 9.3 能否实现通话中转人工坐席？

**可以。** 平台支持通话中转人工坐席：
- 任务级配置：`csStaffGroupId`（坐席组）、`transferPhoneChooseType`（转接方式）、`csDistributionMethod`（分配方式）
- 线路要求：线路的 `supportCsSeat` 需为 `true`
- 运行时接口：`/apiEngine/csCallWaiting/csWaitingCallTransfer` 实现通话转接

### 9.4 关于并发控制

创建任务时通过 `concurrencyQuota`（线路并发数）和 `robotCount`（BOT 数量）控制并发规模，可设置为 1 实现单并发，避免大量同时外呼。

---

## 十、附录：完整接口速查表

### 10.1 robotCallJob 任务相关

```
/apiEngine/robotCallJob/create                    POST  创建任务
/apiEngine/robotCallJob/modify                    POST  修改任务
/apiEngine/robotCallJob/copy                      POST  复制任务
/apiEngine/robotCallJob/delete                    POST  删除任务
/apiEngine/robotCallJob/execute                   POST  执行任务
/apiEngine/robotCallJob/executeJobs               POST  批量执行
/apiEngine/robotCallJob/list                      POST  任务列表
/apiEngine/robotCallJob/getById                   GET   任务详情
/apiEngine/robotCallJob/getStatus                 GET   任务状态
/apiEngine/robotCallJob/getSimpleInfoList         GET   简单信息列表
/apiEngine/robotCallJob/checkName                 POST  检查任务名
/apiEngine/robotCallJob/getJobStatsInfoList       POST  任务统计
/apiEngine/robotCallJob/importUser                POST  导入客户
/apiEngine/robotCallJob/scrmOrgList               POST  SCRM 组织列表
/apiEngine/robotCallJob/scrmWechatList            POST  SCRM 微信列表
/apiEngine/robotCallJob/dynamicRobot              POST  动态 BOT
```

### 10.2 客户数据平台（customerDataPlatform）

```
/customerDataPlatform/account/pageAccount          POST  客户列表
/customerDataPlatform/account/createAccount        POST  新建客户
/customerDataPlatform/account/getAccountInfo       POST  客户详情
/customerDataPlatform/account/updateAccountInfo    POST  更新客户
/customerDataPlatform/account/deleteAccount        POST  删除客户
/customerDataPlatform/account/importAccount        POST  导入客户
/customerDataPlatform/account/exportAccount        POST  导出客户
/customerDataPlatform/account/batchAddTag          POST  批量打标
/customerDataPlatform/account/batchAddAccountToAicc POST 推送到外呼
/customerDataPlatform/customerHeader/getList       POST  字段定义
/customerDataPlatform/tag/*                        POST  标签管理
/customerDataPlatform/crowd/*                      POST  人群管理
```

### 10.3 线路与坐席

```
/apiEngine/tenantPhoneNumber/list                  GET   线路列表
/apiEngine/tenantPhoneNumber/getActive             GET   已激活线路
/apiEngine/tenantPhoneNumber/listAll               GET   所有线路
/apiEngine/csRobot/getStaffStatus                  GET   坐席状态
/apiEngine/csRobot/setCsStatus                     POST  设置坐席状态
/apiEngine/csCallWaiting/csWaitingCallTransfer     POST  转人工
```

### 10.4 实时通信

```
/socket.io/?token=<token>        全局 Socket.IO 通道
/scrmio/?token=<token>           会员中心 Socket.IO 通道
```

---

*文档结束*
