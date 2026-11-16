# 本地 NFC 写入服务

制作工位专用，独立 Node.js 进程。**只监听 `127.0.0.1`，不暴露公网，不持有数据库凭据，只接受一种写操作。**

## 环境要求

- Node.js 18–22（推荐 20 LTS；`nfc-pcsc` 是较老的原生模块，不建议用最新版 Node）
- Windows：安装 ACR122U / ACR1252U 的 PC/SC 驱动
- Linux：`libpcsclite1`、`libpcsclite-dev`、`pcscd`
- `nfc-pcsc` 是原生模块，安装时需要 C++ 构建工具链（Windows 安装 Visual Studio Build Tools）

## 安装

```powershell
cd nfc-service
npm install
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
PORT=8765
PUBLIC_BASE_URL=https://play.example.com
NPC_BASE_URL=https://npc.example.com
ALLOWED_ORIGINS=https://production.example.com
READER_NAME=
CARD_TIMEOUT_MS=15000
ALLOW_HTTP_URL=false
```

- `PUBLIC_BASE_URL`：队员子域地址。写入内容为 `${origin}/r/${ntag_token}`。
- `NPC_BASE_URL`：NPC 子域地址。制作页写 NPC 标签时写入 `${origin}/staff/${npc_token}`。
- `ALLOWED_ORIGINS`：允许调用本服务的制作页 origin，多个用逗号分隔。生产为
  `https://production.<域名>`；本机 mTLS 联调再加 `https://localhost:8443`。
- `READER_NAME`：接了多个读卡器时，用名称（精确或包含匹配）指定一个。留空且检测到多个读卡器会返回 `multiple_readers`。
- `ALLOW_HTTP_URL`：仅本地开发可设为 `true`；生产必须为 `false`，写入 URL 必须是 `https://`。

启动：

```powershell
npm start
```

## HTTP 接口

### `GET /health`

```json
{
  "ok": true,
  "service": "riptag-nfc-writer",
  "version": "1.0.0",
  "reader_connected": true,
  "card_present": false,
  "reader": "ACS ACR122U PICC Interface 0",
  "readers": ["ACS ACR122U PICC Interface 0"],
  "error": null
}
```

### `POST /write`

请求：

队员标签：

```json
{ "ntag_token": "7vQm2Kp9Lx4Tn8Rc6Wb1Za5d" }
```

NPC 标签：

```json
{ "npc_token": "NpcToken..." }
```

`npc_token` 需要配置 `NPC_BASE_URL`，写入 `${NPC_BASE_URL}/staff/${npc_token}`。

成功：

```json
{
  "ok": true,
  "readback_url": "https://play.example.com/r/7vQm2Kp9Lx4Tn8Rc6Wb1Za5d"
}
```

失败统一返回：

```json
{ "ok": false, "code": "card_not_found", "error": "未检测到卡片，请把 NTAG 放稳后重试" }
```

错误码：

| code | HTTP | 含义 |
|---|---|---|
| `invalid_json` | 400 | 请求体不是 JSON |
| `invalid_token` | 400 | token 不是 16-128 位 URL 安全字符 |
| `not_ntag` | 400 | 不是 NDEF 格式的 NTAG215/216 |
| `tag_too_small` | 400 | 标签容量不足 |
| `origin_denied` | 403 | 页面 origin 不在白名单 |
| `write_in_progress` | 409 | 上一次写卡未结束 |
| `multiple_readers` | 409 | 检测到多个读卡器 |
| `card_removed` | 409 | 写卡过程中卡片离开 |
| `card_not_found` | 504 | 超时未放卡 |
| `card_timeout` | 504 | 读卡超时 |
| `readback_mismatch` | 502 | 回读 URL 与目标不一致 |
| `readback_parse_failed` | 502 | 回读数据不是可解析的 NDEF URI |
| `write_failed` | 502 | PC/SC/标签写入失败 |
| `reader_unavailable` | 503 | 没有读卡器 |
| `reader_not_found` | 503 | 指定读卡器不存在 |

## 写入与回读

1. 读 NTAG 能力页（page 3），校验 `E1 10` 和容量。
2. 在 page 4 起写入 Type 2 Tag TLV：`03 <len> <NDEF URI record> FE`，按 4 字节页对齐。
3. URI record 使用 `https://` 前缀码 `0x04`，记录内容为 `qr_code` 之外的完整 URL。
4. 重新读取整段用户区，解析 NDEF URI，必须与目标 URL **完全一致**才返回成功。
5. 任何失败都不返回 `ok: true`，页面因此不会进入打印步骤。

支持 NTAG215/216（以及兼容的 NTAG21x 容量）。不修改能力页、锁位或配置页。

## 安全

- `server.listen(port, "127.0.0.1")`，不监听 `0.0.0.0`。
- 只开放 `GET /health` 和 `POST /write`。
- 没有数据库连接、数据库凭据、文件系统写接口。
- CORS 白名单只允许制作页 origin。
- 不记录 token 到日志；日志只含读卡器名称和错误信息。
- 服务进程使用制作电脑的普通用户权限运行，不要用管理员/root。

## 手工验证

```powershell
# 服务在线
curl.exe http://127.0.0.1:8765/health

# 放一张 NTAG215 后（token 替换为真实种子 token）
curl.exe -X POST http://127.0.0.1:8765/write `
  -H "Content-Type: application/json" `
  -d "{\"ntag_token\":\"7vQm2Kp9Lx4Tn8Rc6Wb1Za5d\"}"
```

再用手机 NFC 读取标签，确认打开的 URL 与 `readback_url` 一致。

## 开机自启

Windows 可用“任务计划程序”以当前用户身份、登录时运行 `npm start`（工作目录为 `nfc-service`）。Linux 可写 systemd user unit：

```ini
[Unit]
Description=RIP TAG local NFC writer
After=pcscd.service

[Service]
WorkingDirectory=/opt/riptag/nfc-service
ExecStart=/usr/bin/node src/server.js
Restart=on-failure
EnvironmentFile=/opt/riptag/nfc-service/.env

[Install]
WantedBy=default.target
```
