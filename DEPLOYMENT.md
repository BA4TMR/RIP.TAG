# 撕名牌现场技术系统 · Ubuntu 24.04 部署指南（v1.1）

本文对应发布包 `riptag-v1.1-ubuntu24.04.tar.gz`，目标系统 **Ubuntu 24.04 LTS**。
部署完成后：

- 队员/NPC 使用普通 HTTPS，**不会请求客户端证书**；
- 技术台主控和制作工位（页面 + API）使用客户端证书 mTLS；
- 首次启动没有活动数据时，主控台自动进入“组数 / 每组人数”向导；
- FastAPI/Uvicorn 只监听 `127.0.0.1:8000`，由 Nginx 对外；
- SQLite 使用 WAL，单机支持 6 组 / 60 人 / 约 70 台手机。

## 1. 架构与域名

使用四个子域，推荐结构：

| 子域 | 用途 | 客户端证书 |
|---|---|---|
| `play.example.com` | 队员 `/r/`、`/home`、扫码、击杀 | 不需要 |
| `npc.example.com` | NPC `/staff/`、看板、补注册、加分 | 不需要 |
| `console.example.com` | 技术台 `/console-<secret>/` | **必须** |
| `production.example.com` | 制作工位 `/production-<secret>/` + `/api/production/*` | **必须** |

```text
浏览器（队员/NPC） ── 443 普通 HTTPS ── Nginx(play/npc) ─┐
技术台设备 ── 443 + 客户端证书 ── Nginx(console)        ─┼─ 127.0.0.1:8000 Uvicorn ─ SQLite
制作工位   ── 443 + 客户端证书 ── Nginx(production)      ─┘
```

> 制作工位页面已集成到主项目；NFC 写卡由可选的本地 `nfc-service` 提供，
> 打印使用浏览器 A6 标签。页面和 API 都要求客户端证书。

## 2. 服务器前提

- Ubuntu 24.04 LTS，建议 2 vCPU / 2 GB 内存 / 20 GB 磁盘以上；
- 公网 IPv4（可选 IPv6）；
- 四个子域的 A/AAAA 记录都指向服务器；
- 安全组/防火墙放行 **80/443**；
- 一个可接收证书通知的邮箱；
- 技术台和制作电脑各安装一张客户端证书。

先更新系统并准备基础工具（安装脚本会自动做）：

```bash
sudo apt update
sudo apt install -y curl rsync tar openssl
```

时间同步：

```bash
sudo timedatectl set-timezone Asia/Shanghai
sudo timedatectl set-ntp true
timedatectl
```

## 3. 上传并解压发布包

在本地生成或获取：

```text
dist/riptag-v1.1-ubuntu24.04.tar.gz
dist/riptag-v1.1-ubuntu24.04.tar.gz.sha256
```

上传后校验：

```bash
sha256sum -c riptag-v1.1-ubuntu24.04.tar.gz.sha256
tar -xzf riptag-v1.1-ubuntu24.04.tar.gz
cd riptag
```

发布包**不包含**：

- `.env`、`.venv/`、`data/activity.db`、`output/`、`backups/`；
- `.local-certs/`、任何私钥、`.p12`；
- Windows 本地 Nginx / 测试证书。

发布包包含应用、测试、部署模板、演示种子和文档。

## 4. 一键安装应用

以 root 运行安装脚本。四个域名必须互不相同：

```bash
cd ~/riptag

sudo PLAY_DOMAIN=play.ba4tmr.cn \
     NPC_DOMAIN=npc.example.com \
     CONSOLE_DOMAIN=console.ba4tmr.cn \
     PRODUCTION_DOMAIN=production.ba4tmr.cn \
     bash deploy/install_ubuntu.sh
```

可选参数：

```bash
# 使用固定安装目录
APP_DIR=/opt/riptag

# 已有 console/production secret（不填则各自自动生成 48 位十六进制随机值）
CONSOLE_SECRET=<至少20位随机值>
PRODUCTION_SECRET=<另一个至少20位随机值>

# 已有客户端 CA bundle（中间 CA 在前、根 CA 在后）
CLIENT_CA_BUNDLE=/root/riptag-client-ca.crt

# 安装完成后立即创建一个 NPC 令牌
NPC_NAME="NPC-A"

# 国内镜像缺少 pydantic-core 时指定可用的 PyPI 镜像（脚本失败会自动重试官方源）
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

> 四个域名必须写对；如果 `/opt/riptag/.env` 已存在，安装脚本会校验
> `PUBLIC_BASE_URL` / `NPC_BASE_URL` 是否与本轮参数一致，不一致会停止并要求先修正
> `.env`，避免 Nginx 域名和应用域名不一致。

脚本会：

1. 安装 `python3-venv`、Nginx、Certbot、SQLite、rsync、UFW 等；
2. 创建系统用户 `riptag`；
3. 复制项目到 `/opt/riptag`；
4. 生成生产 `.env`（`APP_ENV=production`、HTTPS、Secure Cookie、关闭开发绕过、
   随机 `CONSOLE_SECRET` / `PRODUCTION_SECRET`，`SEED_ON_STARTUP=false`）；
5. 创建虚拟环境并安装 `requirements.txt`；
6. 安装并启动 `riptag.service`；
7. 安装每 10 分钟运行的备份 cron；
8. 生成 `/etc/nginx/sites-available/riptag`；
9. 如果证书和客户端 CA 已存在，则启用 Nginx 并 reload。

脚本**不会自动申请证书**，因为必须先完成 DNS 解析。安装结束会打印剩余步骤。

检查应用：

```bash
sudo systemctl status riptag --no-pager
curl -sS http://127.0.0.1:8000/healthz
sudo journalctl -u riptag -n 100 --no-pager
```

首次启动**不会自动导入演示种子**。完成证书、Nginx 和 mTLS 后，打开主控台，
系统会进入“首次启用”向导，询问组数和每组人数并自动生成数据库内容、组别、
NTAG token 和二维码。也可以按第 8 节导入已有正式 CSV。

## 5. 申请 HTTPS 服务器证书

四个子域都要先能解析到服务器：

```bash
dig +short play.example.com
dig +short npc.example.com
dig +short console.example.com
dig +short production.example.com
```

使用 Certbot Nginx 插件申请 SAN 证书（会写入默认 Nginx 配置，之后替换为本项目配置）：

```bash
sudo certbot --nginx \
  -d play.example.com \
  -d npc.example.com \
  -d console.example.com \
  -d production.example.com \
  --agree-tos -m admin@example.com
```

成功后证书通常在：

```text
/etc/letsencrypt/live/play.example.com/fullchain.pem
/etc/letsencrypt/live/play.example.com/privkey.pem
```

如果使用已有证书（例如企业证书），把 fullchain/privkey 放到对应路径，并修改
`/etc/nginx/sites-available/riptag` 中的 `ssl_certificate` / `ssl_certificate_key`。

验证续期：

```bash
sudo certbot renew --dry-run
```

> 客户端证书（技术台/制作工具）与这里的服务器证书是两套体系。服务器证书由
> Let's Encrypt 或企业 CA 签发；客户端证书由独立的客户端 CA 签发，两者不要混用。

## 6. 配置客户端 CA（mTLS）

### 6.1 使用项目脚本生成客户端 CA（推荐新项目）

在可信管理电脑（不是公网服务器）执行：

```bash
cd deploy/certs
chmod +x issue-admin-cert.sh

P12_PASSWORD='主控导出密码' ./issue-admin-cert.sh main-control
P12_PASSWORD='制作工具导出密码' ./issue-admin-cert.sh production-tool
```

把生成的 **CA 公钥证书**上传服务器：

```bash
sudo install -d -m 755 /etc/nginx/certs
sudo install -m 644 riptag-client-ca.crt /etc/nginx/certs/riptag-client-ca.crt
```

把 `main-control.p12` 安装到主控设备，`production-tool.p12` 安装到制作电脑。
CA 私钥 `.key` 离线保存，绝不放在服务器。

### 6.2 使用已有的 BA4TMR 证书

你提供的：

- `单元力证书-BA4TMR.cer`：**客户端证书**（EKU 为 Client Authentication）；
- `RootCA.cer`：根 CA `CN=BA4TMR-RTSRV-R1-CA`；
- 同目录的 `G1-CA.cer`：签发客户端证书的中间 CA `CN=BA4TMR-SUB-CA-G1`。

服务器只需要 CA 链，不需要客户端私钥。把中间 CA 放在前、根 CA 放在后：

```bash
# 转成 PEM（openssl 可自动识别 PEM/DER）
openssl x509 -in G1-CA.cer  -out /tmp/ba4tmr-sub-ca.pem
openssl x509 -in RootCA.cer -out /tmp/ba4tmr-root-ca.pem

# 顺序不能反：issuing intermediate first, root last
sudo install -d -m 755 /etc/nginx/certs
sudo sh -c 'cat /tmp/ba4tmr-sub-ca.pem /tmp/ba4tmr-root-ca.pem \
  > /etc/nginx/certs/riptag-client-ca.crt'
sudo chmod 644 /etc/nginx/certs/riptag-client-ca.crt
```

Nginx 的 `ssl_client_certificate` 会把这些 CA 名称放进 TLS `CertificateRequest`；
如果根在前、中间 CA 在后，部分浏览器不会自动提供由中间 CA 签发的客户端证书。
`ssl_verify_depth 2` 允许 leaf → G1 → root。

主控设备需要安装带私钥的客户端证书。你提供的 Windows 证书库中已有
`CN=单元力`（BA4TMR）证书，可以直接在 Edge/Chrome 使用。换设备时导出 `.p12` 再安装：

```powershell
$pass = ConvertTo-SecureString '导出密码' -AsPlainText -Force
Export-PfxCertificate `
  -Cert 'Cert:\CurrentUser\My\<thumbprint>' `
  -FilePath main-control.p12 -Password $pass
```

本地联调时确认端口只绑定回环地址，不要把制作/主控端口发给普通队员。

## 7. 启用 Nginx

确认证书和 CA 都已存在：

```bash
sudo test -f /etc/letsencrypt/live/play.example.com/fullchain.pem
sudo test -f /etc/nginx/certs/riptag-client-ca.crt
sudo nginx -t
sudo ln -sfn /etc/nginx/sites-available/riptag /etc/nginx/sites-enabled/riptag
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

确认四个 vhost：

```bash
sudo nginx -T | grep -E 'server_name|ssl_verify_client' | head -20
```

预期：

```text
play.example.com         ssl_verify_client off
npc.example.com          ssl_verify_client off
console.example.com      ssl_verify_client on
production.example.com   ssl_verify_client on
```

防火墙：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

不要对公网开放 8000。Uvicorn 已绑定 `127.0.0.1`。

## 8. 初始化活动数据

### 8.1 首次启用向导（推荐，不需要 CSV）

数据库没有制作任务时，打开：

```text
https://console.<域名>/console-<CONSOLE_SECRET>/
```

主控台会自动显示“首次启用”向导：

1. 选择组数（1-6）；
2. 选择每组人数（1-10）；
3. 确认总人数（≤60）；
4. 点击“生成活动数据”。

系统会在同一个事务中创建：

- `groups`：按红/蓝/黄/绿/紫/橙顺序生成组名和组色；
- `production_tasks`：每位队员一条，含唯一 `ntag_token` 和唯一 6 位 `qr_code`；
- 活动轮次和审计记录 `activity_initialized`。

生成后制作工位立即可见全部任务，主控看板也会显示计划总人数。
数据库文件在第一次启动时由应用创建（默认 `/opt/riptag/data/activity.db`）。

> 向导只允许执行一次。已有 `production_tasks` 时不会重复生成。

### 8.2 可选：导入正式 CSV

如果已经有一份正式种子（例如提前用 `generate_seed` 生成）：

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.generate_seed \
  --per-group 10 \
  --out data/seed.csv \
  --urls-out output/ntag_urls.csv \
  --base-url https://play.example.com \
  --force
```

格式固定：

```csv
ntag_token,qr_code,group_no,group_color
<随机 token>,<6 位码>,红队,#E53935
```

在空库上导入：

```bash
sudo systemctl stop riptag
sudo -u riptag rm -f data/activity.db data/activity.db-wal data/activity.db-shm
sudo -u riptag .venv/bin/python -m app.seed --csv data/seed.csv
sudo systemctl start riptag
```

> `app.seed` 要求 `production_tasks` 为空；已有任务时不会覆盖。默认
> `SEED_ON_STARTUP=false`，因此服务启动本身不会自动导入演示数据。

### 8.3 迁移“前期已制作/已关联”的数据

如果你已经在本地运行过名牌制作工具，并把 NTAG/二维码关联写入了本地
`production_tasks`，**发布包本身不包含数据库**，需要先导出关联：

```bash
cd /path/to/本地项目
python -m scripts.manage export-seed --out output/seed-export.csv --force
python -m scripts.manage export-production --out output/production-status.csv
```

- `seed-export.csv` 包含 `ntag_token, qr_code, group_no, group_color`，这是部署必须带上的
  关联映射；
- `production-status.csv` 包含 `written_at/printed_at/done_at/last_error`，仅用于制作进度
  留档；**队员注册、击杀、看板都不依赖制作状态**，可以不导入。

把 `seed-export.csv` 作为服务器的 `data/seed.csv`，按 8.2 清空演示数据库并重新导入。
如果之前只完成了“写卡 + 打印”，没有把状态写入数据库，也可以直接使用当时的
`data/seed.csv`；只要 `ntag_token/qr_code/group_no` 与实体标签一致即可。

> 当前发布包内是 24 人演示种子；如果你看到的是演示 token，不是你的实际关联，
> 说明导出文件没有放进发布包。请用上述命令从你实际运行过的数据库导出，或把你
> 已经生成的正式 `seed.csv` 替换进去。

检查：

```bash
sudo -u riptag .venv/bin/python -m scripts.manage show-stats
```

## 9. NTAG 与二维码标签

### 9.1 写 NTAG

使用 `output/ntag_urls.csv`，写入 NDEF URL：

```text
https://play.example.com/r/<ntag_token>
```

要求：

- NTAG215/216；
- 写后回读 URL 完全一致；
- 不要公开 token；它就是登录凭证；
- 不要批量写完后不验证。

### 9.2 打印击杀码标签

推荐直接使用制作工位页面：每件任务点“打印标签”，浏览器打开 A6 标签
（二维码 + 6 位明文码），打印后贴名牌**背面**。页面会记录 `printed`。

也可以批量生成 A4 标签：

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.print_cards \
  --out output/name_tags.html
```

打开 HTML，A4 打印，10 张/页。二维码内容只是 6 位击杀码。
身份码 `UID:<member_id>` 只在手机页面展示，不打印到实体名牌。

## 10. 创建 NPC 令牌

推荐在主控台操作：

1. 打开 `console.<域名>`，进入「NPC 管理」；
2. 输入 NPC 名称，点「创建 NPC」；
3. 复制生成的 `https://npc.<域名>/staff/<token>`；
4. 到制作工位点「NPC 标签」，选择该 NPC，点「写入 NPC 标签」。

NPC 在数据库中的击杀码固定为 **`XXXXXX`**，只是占位符，不参与队员击杀。

也可以命令行创建：

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.manage create-npc --name "NPC-A"
```

把 URL 写入 NPC 专用 NTAG。不要发群、不要截图。停用：

```bash
sudo -u riptag .venv/bin/python -m scripts.manage list-npc
sudo -u riptag .venv/bin/python -m scripts.manage disable-npc --id 2
```

制作工位写 NPC 标签要求本地 `nfc-service/.env` 配置：

```dotenv
PUBLIC_BASE_URL=https://play.<域名>
NPC_BASE_URL=https://npc.<域名>
ALLOWED_ORIGINS=https://production.<域名>
```

没有本地 NFC 服务时，也可以用 NFC Tools 等工具写入 NPC URL。

## 11. 验收测试

### 11.1 普通用户无客户端证书

```bash
curl -sS https://play.example.com/healthz
curl -I https://play.example.com/
curl -I https://npc.example.com/staff
```

应正常返回（队员首页/健康检查 200，NPC 未登录会跳转/401，而**不是**证书错误）。

### 11.2 主控 mTLS

```bash
SECRET=$(grep '^CONSOLE_SECRET=' /opt/riptag/.env | cut -d= -f2-)

# 无证书：Nginx 应拒绝（400/403，不是登录页）
curl -k -I https://console.example.com/console-$SECRET/

# 有证书：200
curl --cert main-control.crt --key main-control.key \
  --cacert /etc/ssl/certs/ca-certificates.crt \
  -I https://console.example.com/console-$SECRET/
```

浏览器验证：

- 在技术台设备安装 `.p12`；
- 打开 `https://console.example.com/console-<secret>/`；
- 选择 **CN=单元力** 或 `main-control` 证书；
- 无证书设备不能进入主控。

### 11.3 制作工位 mTLS

```bash
P_SECRET=$(grep '^PRODUCTION_SECRET=' /opt/riptag/.env | cut -d= -f2-)

# 无证书：Nginx 应拒绝
curl -i https://production.example.com/production-$P_SECRET/        # 400/403

# 有证书：制作工位页面 200
curl --cert production-tool.crt --key production-tool.key \
  -I https://production.example.com/production-$P_SECRET/           # 200

# 同一证书访问 API / 二维码
curl --cert production-tool.crt --key production-tool.key \
  https://production.example.com/api/production/tasks               # 200
```

浏览器打开制作工位后：

1. 写入 NTAG：本机启动 `nfc-service`，页面显示读卡器在线；放卡后回读成功才进入“已写卡”；
2. 打印标签：浏览器打开 A6 打印窗口，二维码由主项目生成；
3. 完成贴牌后点“完成贴牌”；
4. 无读卡器时使用“手动标记已写卡”，失败可“记录异常”并重置。

NFC 服务只监听 `127.0.0.1:8765`，不随主服务启动；生产工位 origin 必须加入
`nfc-service/.env` 的 `ALLOWED_ORIGINS`（例如 `https://production.<域名>`）。

### 11.4 业务验收

- 手机碰 NTAG → 注册 → `/home`；再次碰标签恢复登录；
- 扫码/手输 → 二次确认 → 击杀；
- 已淘汰二次扫不新增记录；
- 两台手机并发扫同一目标只有一条成功；
- 自爆统计正确；
- 主控撤销 → 统计回滚 + 被击杀者复活；
- 主控复活 → 历史统计不回滚，复活后再杀正常；
- NPC 只能补注册、加分；不能撤销/导出；
- SSE 看板实时刷新；
- CSV 导出可打开；
- 软重置保留姓名；硬重置清空姓名并让旧 Cookie 失效。

## 12. 日常运维

### 服务与日志

```bash
sudo systemctl status riptag
sudo journalctl -u riptag -f
sudo systemctl restart riptag
sudo systemctl reload nginx
```

### 备份

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.backup \
  --out /var/backups/riptag --keep 48
```

自动任务在 `/etc/cron.d/riptag-backup`。活动前另做一次冷备，并把备份复制到另一台机器。

### 导出

主控台 → 系统与导出：

- 击杀记录 CSV
- 积分记录 CSV
- 审计日志 CSV
- 本轮统计 CSV

### 重置

- 默认软重置：新 round，保留姓名/任务绑定，已注册队员恢复存活；
- 硬重置：清空姓名、撤销所有队员 Cookie；`production_tasks` 和历史击杀保留；
- 重置前先导出 CSV + 备份数据库。

### 制作状态

制作状态在 `production_tasks`，由制作工位页面（或 `/api/production/*`）通过 mTLS 修改；
不参与活动统计，也不影响注册。查看：

```bash
curl --cert production-tool.crt --key production-tool.key \
  https://production.example.com/api/production/status
```

## 13. 升级与回滚

### 升级

```bash
sudo systemctl stop riptag
sudo -u riptag .venv/bin/python -m scripts.backup --out /var/backups/riptag --keep 20

# 解压新版本到临时目录，保留 data/.env
sudo rsync -a --exclude '.env' --exclude 'data/activity.db*' \
  /tmp/riptag-new/ /opt/riptag/
sudo chown -R riptag:riptag /opt/riptag
sudo -u riptag .venv/bin/pip install -r requirements.txt
sudo systemctl start riptag
```

应用启动时会自动执行 `ensure_schema`，将旧库补齐到 v1.1 的
`production_tasks/members` 结构。

### 回滚

恢复上一版代码后重启，并恢复升级前备份的 `activity.db`（如数据库结构已变）。
发布包/备份/变更记录要一起保留。

## 14. 故障排查

| 现象 | 处理 |
|---|---|
| `riptag` 启动失败 | `journalctl -u riptag -n 100`；检查 `.env` 的 APP_ENV、secret、HTTPS 域名 |
| `pydantic-core==2.23.4` 找不到 | 镜像源缺包；`pip install -i https://pypi.org/simple pydantic-core==2.23.4`，或重跑安装脚本让其回退官方源 |
| 安装脚本报 `.env` 域名不一致 | 修正 `/opt/riptag/.env` 的 `PUBLIC_BASE_URL`/`NPC_BASE_URL`，或备份后移走 `.env` 重跑 |
| 普通页面要求客户端证书 | 是否访问了 `console` 子域；`play`/`npc` 必须是 `ssl_verify_client off` |
| 主控无证书也能进 | 检查 Nginx 对应 vhost 是否 `on`，是否还在使用默认站点/旧配置 |
| 主控有证书但 400/403 | CA bundle 顺序（中间 CA 在前、根 CA 在后）、证书是否选对、`ssl_verify_depth` |
| 浏览器不弹证书选择 | `CertificateRequest` 未包含签发 CA；调整 CA bundle 顺序；确认设备证书有私钥 |
| 制作工位/API 403 | 访问了 play 子域，或没有携带客户端证书 |
| 制作页显示“技术台尚未初始化” | 先到主控台完成首次启用向导 |
| 制作页二维码不显示 | 检查 `PRODUCTION_SECRET` 路径和 Nginx `/static/`、页面 location |
| Chrome/Huawei 浏览器无法启动摄像头 | 确认 HTTPS、地址栏摄像头权限；更新后使用“拍照识别”或手输码。若仍报 `NotAllowedError`，在系统设置中允许浏览器使用相机 |
| 队员 Cookie 丢失 | 必须 HTTPS；`COOKIE_SECURE=true`；不要用无痕模式 |
| 碰标签 404 | NDEF URL 是否为 play 子域；token 是否在 `production_tasks` |
| 注册后没有成员 | 查看启动日志/任务；`members.production_task_id` 是否建立 |
| SSE 不刷新 | Nginx 是否关闭 buffering；是否单 worker；NPC 子域是否放行 `/api/stream` |
| 数据库 locked | 只能一个 Uvicorn worker；确认无第二个进程；不要删除 WAL |
| 证书续期失败 | `certbot renew --dry-run`；80 端口/DNS；Certbot Nginx 插件配置 |

## 15. 安全检查清单

- [ ] `APP_ENV=production`、`COOKIE_SECURE=true`、真实 HTTPS 域名；
- [ ] 四个子域按 play/npc 无证书、console/production mTLS 配置；
- [ ] `CONSOLE_SECRET` 和 `PRODUCTION_SECRET` 独立、随机、≥20 位；
- [ ] 首次启动向导已生成正确组数/人数，制作工位任务数与之一致；
- [ ] `console` 无证书返回 400/403，有证书返回 200；
- [ ] `production` 页面/API 无证书返回 400/403，有证书返回 200；
- [ ] 技术台/制作证书来自同一客户端 CA，CA 私钥离线；
- [ ] Uvicorn 只监听 `127.0.0.1`，8000 未对公网开放；
- [ ] Nginx 与 Uvicorn 访问日志关闭，避免 token 落盘；
- [ ] `.env` 权限 600，属主 riptag；
- [ ] 备份 cron 正常运行，备份已复制出服务器；
- [ ] 正式种子 60 人，6 组，每组 ≤10；
- [ ] 活动前用 iPhone 和 Android 各完成一次注册/恢复/击杀测试；
- [ ] 主控设备完成一次撤销、复活、导出、重置演练。

## 16. 手动安装（不使用脚本）

自动脚本失败或需要自定义路径时：

```bash
sudo apt install -y python3 python3-venv nginx certbot python3-certbot-nginx sqlite3 rsync
sudo useradd --system --create-home --shell /usr/sbin/nologin riptag
sudo mkdir -p /opt/riptag /var/backups/riptag
sudo chown -R riptag:riptag /opt/riptag /var/backups/riptag

# 复制项目后
cd /opt/riptag
sudo -u riptag python3 -m venv .venv
sudo -u riptag .venv/bin/pip install -r requirements.txt

sudo cp .env.example .env
# 编辑 .env 为 production；参见第 4 节脚本生成内容
sudo chown riptag:riptag .env && sudo chmod 600 .env

sudo cp deploy/riptag.service /etc/systemd/system/riptag.service
sudo systemctl daemon-reload
sudo systemctl enable --now riptag
```

其余步骤（证书、CA、Nginx、种子、验收）与第 5-11 节一致。
更细的 Nginx/证书说明见 `deploy/README.md`。
