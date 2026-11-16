# 部署与现场运维手册（v1.1）

面向 Linux 云服务器（Ubuntu 22.04/24.04 推荐）

## 0. 子域与证书隔离

| 子域 | 角色/用途 | 客户端证书 |
|---|---|---|
| `play.example.com` | 队员页面、`/r/`、`/home`、击杀 API | 不需要 |
| `npc.example.com` | NPC 场务 `/staff/`、看板/加分 | 不需要 |
| `console.example.com` | 主控台 `/console-<secret>/` | **必须**，`ssl_verify_client on` |
| `production.example.com` | 制作工位 `/production-<secret>/` + API | **必须**，`ssl_verify_client on` |

主控和制作工具共用一套**私有客户端 CA**；`play` / `npc` 只使用SSL服务器
证书，不向队员/NPC 请求客户端证书。

```text
玩家/NPC ── HTTPS(443) ── Nginx(play/npc) ─┐
主控设备 ─ HTTPS(443)+客户端证书 ─ Nginx(console) ─┤
制作工具 ─ HTTPS(443)+客户端证书 ─ Nginx(production) ─┴─ 127.0.0.1:8000 Uvicorn
                                                            └─ SQLite WAL
```

**只启动一个 Uvicorn worker**：SSE 广播和失败码限速是进程内状态，SQLite 单写者。
Uvicorn 只绑定回环，Nginx 覆盖写入 `X-Client-Verify`，后端只接受 `SUCCESS`。

## 1. 服务器准备

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx certbot python3-certbot-nginx sqlite3 curl
sudo useradd --system --create-home --shell /usr/sbin/nologin riptag
sudo mkdir -p /opt/riptag /var/backups/riptag
sudo chown -R riptag:riptag /opt/riptag /var/backups/riptag
cd /opt/riptag
sudo -u riptag python3 -m venv .venv
sudo -u riptag .venv/bin/pip install --upgrade pip
sudo -u riptag .venv/bin/pip install -r requirements.txt
```

## 2. DNS 与 HTTPS

为四个子域建立 A/AAAA 记录，指向服务器。申请包含四个名字的 SAN 证书（或
`*.example.com` 通配符）：

```bash
sudo certbot --nginx \
  -d play.example.com \
  -d npc.example.com \
  -d console.example.com \
  -d production.example.com
```

证书会落在 `/etc/letsencrypt/live/play.example.com/`（主域名为 play）。如使用通配符，
修改 `deploy/nginx.conf.example` 中的证书路径。

## 3. 环境配置

```bash
cd /opt/riptag
cp .env.example .env
python3 - <<'PY'
import secrets
print("CONSOLE_SECRET=" + secrets.token_urlsafe(32))
print("PRODUCTION_SECRET=" + secrets.token_urlsafe(32))
PY
```

编辑 `.env`：

```dotenv
APP_ENV=production
PUBLIC_BASE_URL=https://play.example.com
NPC_BASE_URL=https://npc.example.com
DATABASE_URL=sqlite:////opt/riptag/data/activity.db
COOKIE_SECURE=true
CONSOLE_SECRET=<32 位随机值>
PRODUCTION_SECRET=<另一个 32 位随机值>
ALLOW_INSECURE_CONSOLE=false
ALLOW_INSECURE_PRODUCTION=false
SEED_CSV=data/seed.csv
SEED_ON_STARTUP=false
```

`PUBLIC_BASE_URL` 是 NTAG 写卡使用的 play 子域。生产配置校验会拒绝默认 secret、
`COOKIE_SECURE=false`、开发 mTLS 绕过，以及非 HTTPS 公网地址。

```bash
sudo chown -R riptag:riptag /opt/riptag
sudo chmod 600 /opt/riptag/.env
```

## 4. 种子、NTAG、标签

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.generate_seed \
  --per-group 10 \
  --out data/seed.csv \
  --urls-out output/ntag_urls.csv \
  --base-url https://play.example.com \
  --force
```

- 6 组 × 10 人，共 60 条 `production_tasks`。
- CSV 字段固定：`ntag_token,qr_code,group_no,group_color`。
- 首次启动自动导入；手动导入使用 `python -m app.seed --csv data/seed.csv`。
- 种子只创建 `production_tasks`；队员注册后才创建 `members`。
- 写 NTAG：NDEF URL `https://play.example.com/r/<ntag_token>`，写后回读验证。
- 打印击杀码标签：`python -m scripts.print_cards --out output/name_tags.html`。

## 5. NPC 令牌

```bash
sudo -u riptag .venv/bin/python -m scripts.manage create-npc --name "NPC-A"
```

脚本使用 `NPC_BASE_URL`，生产输出应为 `https://npc.example.com/staff/<token>`。
把 URL 写入 NPC 专用 NTAG。
停用：`python -m scripts.manage disable-npc --id N`。

## 6. 客户端 CA 与 .p12

在可信管理电脑上（不是公网服务器）：

```bash
cd deploy/certs
chmod +x issue-admin-cert.sh
P12_PASSWORD='至少 12 位的导出密码' ./issue-admin-cert.sh main-control
P12_PASSWORD='另一个密码' ./issue-admin-cert.sh production-tool
```

输出在 `deploy/certs/riptag-certs/`。只把 CA 公钥证书上传服务器：

```bash
sudo install -d -m 755 /etc/nginx/certs
sudo install -m 644 riptag-client-ca.crt /etc/nginx/certs/riptag-client-ca.crt
```

如果客户端证书不是由这个根直接签发，而是经过中间 CA，把**签发中间 CA 放在前、根 CA 放在后**合成一个文件：

```bash
sudo sh -c 'cat riptag-sub-ca.pem riptag-root-ca.pem \
  > /etc/nginx/certs/riptag-client-ca.crt'
```

顺序很重要：Nginx 会把该文件中的 CA 名称放进 TLS `CertificateRequest`；根在前时，
部分浏览器不会自动提供由中间 CA 签发的客户端证书。`ssl_verify_depth 2` 允许
leaf → 中间 CA → 根 CA。

主控设备安装 `main-control.p12`，制作工具设备安装 `production-tool.p12`。CA 私钥
`.key` 离线保存，绝不放到服务器或仓库。详细步骤见 [`certs/README.md`](certs/README.md)。

## 7. Nginx

```bash
cd /opt/riptag
CONSOLE_SECRET=$(grep '^CONSOLE_SECRET=' .env | cut -d= -f2-)
PRODUCTION_SECRET=$(grep '^PRODUCTION_SECRET=' .env | cut -d= -f2-)
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/riptag
sudo sed -i \
  -e 's/play\.example\.com/play.你的域名/g' \
  -e 's/npc\.example\.com/npc.你的域名/g' \
  -e 's/console\.example\.com/console.你的域名/g' \
  -e 's/production\.example\.com/production.你的域名/g' \
  /etc/nginx/sites-available/riptag
sudo sed -i \
  -e "s/__CONSOLE_SECRET__/${CONSOLE_SECRET}/g" \
  -e "s/__PRODUCTION_SECRET__/${PRODUCTION_SECRET}/g" \
  /etc/nginx/sites-available/riptag
sudo ln -sf /etc/nginx/sites-available/riptag /etc/nginx/sites-enabled/riptag
sudo nginx -t && sudo systemctl reload nginx
```


## 8. systemd

```bash
sudo cp deploy/riptag.service /etc/systemd/system/riptag.service
# 项目不在 /opt/riptag 时编辑 service 路径
sudo systemctl daemon-reload
sudo systemctl enable --now riptag
sudo systemctl status riptag --no-pager
sudo journalctl -u riptag -f
```

服务只监听 `127.0.0.1:8000`，单 worker，`--no-access-log`。健康检查：

```bash
curl -sS http://127.0.0.1:8000/healthz
```

## 9. 防火墙

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 10. 备份

```bash
cd /opt/riptag
sudo -u riptag .venv/bin/python -m scripts.backup --out /var/backups/riptag --keep 48
```

安装定时任务：

```bash
sudo touch /var/log/riptag-backup.log
sudo chown riptag:riptag /var/log/riptag-backup.log
sudo cp deploy/riptag-backup.cron /etc/cron.d/riptag-backup
sudo chmod 644 /etc/cron.d/riptag-backup
```

活动前另做冷备；把备份同步到另一台机器/对象存储。备份和导出含姓名与击杀码，按敏感数据保管。

## 11. 现场运行

### 活动前

1. 首次启动向导生成 6 组/每组 10 人，或导入正式 CSV；核对待制作任务数。
2. 写 NTAG 并回读；打印击杀码标签，贴名牌背面。
3. 创建 NPC 令牌并写入 NPC 标签。
4. 安装主控/制作客户端证书，分别访问 console/production 子域测试。
5. 备份数据库；确认 SSE、导出、重置可用。
6. 提醒队员解锁手机、**关闭无痕模式**；现场移动数据。

### 活动中

- 主控看板与操作同页；误杀用“撤销击杀”，不要改数据库。
- 扫码失败用主控“手动补录”，来源标记 manual 并写审计。
- NPC 只加分；扣分、改判、撤销由主控处理。
- 每 10-15 分钟检查备份和 SSE。
- 制作工具状态变化与主业务完全隔离；不要用制作状态判断队员是否可注册。

### 轮次

- 默认软重置：新 round，保留姓名/绑定，已注册队员恢复存活，击杀/积分从新轮开始。
- 硬重置：清空姓名、撤销所有队员 Cookie；任务保留，可重新注册。
- 历史击杀/积分/审计保留。

## 12. 故障排查

| 现象 | 排查 |
|---|---|
| console 403 | 设备未选证书、CA 不匹配、Nginx 未设 `X-Client-Verify`、访问了 play 子域 |
| play 弹证书选择 | `ssl_verify_client` 是否误设为 on；play 必须是 off |
| production 页面/API 403 | 未带证书或访问了 play 子域 |
| 制作页提示未初始化 | 主控台先完成“组数 / 每组人数”首次启用向导 |
| 队员 Cookie 不生效 | 生产必须 HTTPS；`COOKIE_SECURE=true` |
| 碰标签空白/404 | NDEF URL 是否使用 play 子域；回读 token 是否与 `production_tasks` 一致 |
| 注册后没有成员记录 | 检查 `members.production_task_id`；看启动日志迁移/种子导入是否报错 |
| SSE 不刷新 | Nginx 是否关闭 buffering；单 worker；NPC 子域放行 `/api/stream` |
| 数据库 locked | 只跑一个 Uvicorn worker；确认无第二个进程；不要删除 WAL |
| 制作状态影响看板？ | 不应影响；看板只读 `members/kills/rounds`，任务状态独立 |
| 旧库启动报列错误 | 先备份，再启动一次让 `ensure_schema` 完成 v1.0→v1.1 迁移 |

## 13. 安全边界

- 客户端 CA 私钥泄露可伪造主控/制作证书；离线保存，泄露即换 CA 并重签。
- `.p12` 有密码仍需妥善保管；丢设备时从 Nginx 移除或换 CA。
- 队员/NPC token 是登录凭证：访问日志关闭，不要发群/截图。
- 静态二维码可被拍照复制，这是规则风险；身份码 `UID:<member_id>` 只做同名区分。
- 6 位码限速在进程内，故单 worker；不能横向多 worker。
- CSV 导出做公式注入防护；审计覆盖主控/制作状态操作。
- SQLite 是单机方案；不要多实例同时写同一数据库。

## 14. 制作工位

主项目提供：

- `production_tasks` 表；
- 4 个任务 API + NPC 列表 `/api/production/npcs`；
- 制作工位页面 `/production-<PRODUCTION_SECRET>/`；
- 服务端二维码 SVG 和浏览器 A6 打印；
- NPC 标签写入（技术台创建的 `/staff/<token>`，占位击杀码 `XXXXXX`）。

NFC 写卡是可选的本机 Node 服务（只监听 `127.0.0.1:8765`）；不启动它时可在页面
使用“手动标记已写卡”。物理写卡、打印和 NFC 验收见
[`README-production.md`](README-production.md) 与 `nfc-service/`。
