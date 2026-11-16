# 主控 / 制作 API 客户端证书（mTLS）

主控台和制作工具 API 使用**同一套独立客户端 CA**，与 Let's Encrypt 的服务器证书互不影响。
制作工具再签发 `production-tool` 证书，安装到制作电脑。

## 1. 生成 CA 和第一张主控证书

在一台可信的管理电脑上（推荐 Linux / WSL / Git Bash）：

```bash
cd deploy/certs
chmod +x issue-admin-cert.sh
P12_PASSWORD='一个足够长的导出密码' ./issue-admin-cert.sh main-control
```

输出默认在当前目录的 `riptag-certs/`：

| 文件 | 用途 |
|---|---|
| `riptag-client-ca.crt` | 复制到服务器，供 Nginx `ssl_client_certificate` 使用 |
| `riptag-client-ca.key` | **私钥，离线保存，绝不放到服务器或仓库** |
| `main-control.crt` | 客户端证书 |
| `main-control.key` | 客户端私钥 |
| `main-control.p12` | 安装到主控手机/电脑的证书包，带导出密码 |

为第二台主控设备或制作工具签发另一张：

```bash
P12_PASSWORD='另一个密码' ./issue-admin-cert.sh backup-console
P12_PASSWORD='制作设备密码' ./issue-admin-cert.sh production-tool
```

**不要覆盖已有证书**。脚本会拒绝覆盖；如确需轮换，换一个 `NAME`，更新 Nginx 后再删除旧证书。

## 2. 服务器安装 CA 证书

只复制 CA 的**公钥证书**：

```bash
sudo install -d -m 755 /etc/nginx/certs
sudo install -m 644 riptag-client-ca.crt /etc/nginx/certs/riptag-client-ca.crt
sudo nginx -t && sudo systemctl reload nginx
```

若使用外部 CA（例如 BA4TMR）且客户端证书由中间 CA 签发，需要把
**签发中间 CA 在前、根 CA 在后**合成一个文件，而不是只放根 CA：

```bash
sudo sh -c 'cat ba4tmr-sub-ca.pem ba4tmr-root-ca.pem \
  > /etc/nginx/certs/riptag-client-ca.crt'
```

否则 Nginx 的 `CertificateRequest` 可能只广告根 CA，浏览器不会提供由中间 CA
签发的客户端证书。

Nginx 按子域隔离（详见 `deploy/nginx.conf.example`）：

- `console.example.com`：`ssl_verify_client on;`
- `production.example.com`：`ssl_verify_client on;`
- `play.example.com` / `npc.example.com`：`ssl_verify_client off;`

两个受保护子域都配置：

```nginx
ssl_client_certificate /etc/nginx/certs/riptag-client-ca.crt;
ssl_verify_client on;         # 握手阶段即要求证书
ssl_verify_depth 2;
```

## 3. 安装 .p12 到主控设备

### Windows

1. 双击 `.p12`，选择“当前用户”。
2. 输入导出密码。
3. 证书存储选择“个人”。
4. 重新打开 Edge / Chrome，访问主控 URL 时选择该证书。

### macOS

1. 双击 `.p12`，加入“登录”钥匙串。
2. 在“钥匙串访问”中把证书信任为 SSL 客户端证书（如系统提示）。
3. Safari 访问主控 URL 时选择证书。

### iPhone / iPad

1. 通过 AirDrop、文件 App 或邮件把 `.p12` 传到设备。
2. 设置 → 通用 → VPN 与设备管理 → 安装描述文件/证书，输入密码。
3. 设置 → 通用 → 关于本机 → 证书信任设置，确认该客户端 CA 已信任（如系统要求）。
4. Safari 访问主控 URL，选择“RIP TAG 主控”身份。

> iOS 对客户端证书的选择行为受系统版本影响。活动前务必用**同一台主控设备**完成一次完整登录测试；Nginx 返回 403 时先检查设备是否选中了证书。

## 4. 验证

```bash
# 不带证书应被拒绝
curl -I https://console.example.com/console-<secret>/
curl -i https://production.example.com/api/production/tasks

# 带主控证书应返回 200
curl --cert main-control.crt --key main-control.key \
  -I https://console.example.com/console-<secret>/

# 带制作工具证书应返回 200
curl --cert production-tool.crt --key production-tool.key \
  https://production.example.com/api/production/tasks
```

`curl` 验证的是 Nginx 层；浏览器还要验证设备证书选择。主控台 API 会再次校验 `X-Client-Verify: SUCCESS`，该请求头由 Nginx 覆盖写入。

## 5. 安全注意

- 客户端 CA 私钥一旦泄露，任何人都能自签主控证书。请离线保存；泄露时立即更换 CA 并重新签发全部证书。
- `.p12` 有导出密码仍需妥善保管；丢失设备时从 Nginx 删除对应证书或更换 CA。
- 不要把 `riptag-certs/`、`.p12`、`.key` 提交到 Git；项目 `.gitignore` 已包含这些模式。
