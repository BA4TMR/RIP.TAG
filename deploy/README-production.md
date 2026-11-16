# 制作工位与本地 NFC 说明

这是制作工位的访问链接

```text
https://production.<domain>/production-<PRODUCTION_SECRET>/
```

页面和 `/api/production/*` 都要求 mTLS 客户端证书。页面提供队列、二维码、
A6 打印和状态标记；本地 NFC 写卡由可选的 `nfc-service` 提供。

## 1. 边界

```text
制作工位浏览器 ── HTTPS + 客户端证书 ── Nginx(production)
        │                                  │
        │                                  ├─ /production-<secret>/   页面
        │                                  ├─ /api/production/*       状态 API
        │                                  └─ /static/*               JS/CSS/二维码
        │
        └─ http://127.0.0.1:8765  ── 本地 NFC 服务（可选）
```

- 制作工位 UI、二维码、打印都在主项目中。
- NFC 写卡服务只监听 `127.0.0.1:8765`，不持有数据库凭据。
- 制作状态不参与队员注册、击杀、看板、统计、重置。
- 制作页面不需要登录 Cookie；每次请求都通过 mTLS 认证。
- 首次启用由技术台主控向导生成任务；未初始化时制作页会提示等待。
- 制作页「NPC 标签」可写入技术台创建的 NPC `/staff/<token>`；
  NPC 击杀码固定为 `XXXXXX`，不参与击杀。

## 2. 证书

制作工具与技术台共用客户端 CA：

```bash
cd deploy/certs
P12_PASSWORD='制作设备证书密码' ./issue-admin-cert.sh production-tool
```

把 `production-tool.p12` 安装到制作电脑；服务器仅安装
`/etc/nginx/certs/riptag-client-ca.crt`。

完整配置见 `nginx.conf.example`。

主项目再校验 Nginx 覆盖写入的 `X-Client-Verify: SUCCESS`。

## 3. API 契约

### 拉取任务

```http
GET /api/production/tasks?status=all|pending|in_progress|done
```

返回数组：

```json
[
  {
    "id": 1,
    "ntag_token": "seed-token",
    "qr_code": "ABC123",
    "group_no": "红队",
    "group_color": "#E53935",
    "written_at": null,
    "printed_at": null,
    "done_at": null,
    "last_error": null,
    "stage": "pending",
    "stage_label": "待制作"
  }
]
```

`stage` 由时间戳推导，不存数据库。字段中没有姓名：姓名由队员注册时产生。

### 标记状态

```http
POST /api/production/1/mark
Content-Type: application/json

{"stage": "written"}
```

阶段顺序：

```text
pending --written--> written --printed--> printed --done--> done
```

- `written` 后 `written_at` 写入；
- `printed` 要求 `written_at` 已存在；
- `done` 要求 `printed_at` 已存在；
- 重复提交同一阶段幂等；
- 失败时提交 `{"stage":"written","error":"NFC 读取超时"}`：写入 `last_error`，
  不推进阶段；成功后清空 `last_error`；
- 已完成任务如需重做，先调用 reset。

### 重置

```http
POST /api/production/1/reset
```

清空 `written_at/printed_at/done_at/last_error`，允许重做，写审计。

### 汇总

```http
GET /api/production/status
```

```json
{
  "total": 60,
  "written": 20,
  "printed": 10,
  "done": 5,
  "failed": 1,
  "pending": 39,
  "in_progress": 16,
  "all_done": false
}
```

### NPC 标签列表

```http
GET /api/production/npcs
```

返回技术台创建的启用 NPC，每个都带 `token`、`url` 和固定占位击杀码 `XXXXXX`。
制作页「NPC 标签」使用本地 `nfc-service` 写入 `/staff/<token>`：

```json
{ "npc_token": "<npc_token>" }
```

`nfc-service/.env` 需要配置：

```dotenv
NPC_BASE_URL=https://npc.<域名>
```

## 4. 制作流程

页面操作：

1. 打开 `/production-<PRODUCTION_SECRET>/`，选择技术台生成的任务；
2. 放 NTAG → 点“写入 NTAG”，本地服务写卡并回读；成功后自动标记 `written`；
3. 点“打印标签”打开 A6 窗口，二维码/明文码打印后贴名牌背面；
4. 点“已打印”标记 `printed`；
5. 点“完成贴牌”标记 `done`；
6. 失败可“记录异常”并“重置任务”；无读卡器时可手动标记已写卡。

API 等价流程：

1. `GET tasks?status=pending` 拉取队列。
2. 写 NTAG，回读 NDEF URL 与 `https://play.<domain>/r/<ntag_token>` 完全一致。
3. 成功后 `mark written`；失败则 `mark written + error`，不要进入打印。
4. 打印二维码（内容 `qr_code`）和明文码，贴名牌背面。
5. 成功后 `mark printed`。
6. 贴牌完成，`mark done`。
7. 任何阶段失败都记录 `last_error`，可由 reset 重做。

制作状态是物理制作进度，不是队员是否已注册，更不是击杀/存活状态。
