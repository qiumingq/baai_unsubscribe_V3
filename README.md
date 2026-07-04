# BAAI-CFTS 退订系统 - 阿里云轻量应用服务器(alinux)部署修复包 v4

适用环境：Alibaba Cloud Linux (alinux)，内核 5.10.134-19.1.al8.x86_64，轻量应用服务器

## 这个压缩包包含什么
- app.py                          Flask后端（已修复：邮件发送异常不再被吞掉，新增/api/test-email测试接口）
- templates/unsubscribe.html      退订页面（已修复：点击退订后正确展示"退订成功"提示）
- requirements.txt                依赖清单
- baai-unsubscribe.service        systemd 服务配置（用gunicorn常驻运行，替代Render上的托管方式）
- nginx_baai-unsubscribe.conf     nginx 反向代理配置（把80端口请求转发给内部8000端口的gunicorn）
- deploy.sh                       一键部署脚本

## 关于你之前遇到的两个问题，在alinux环境下的具体排查

### 问题1：点击退订不显示成功页面
和部署环境无关，是前端JS逻辑缺陷，已在 templates/unsubscribe.html 里修复，直接覆盖替换即可生效，
替换后记得执行 `systemctl restart baai-unsubscribe` 让新代码生效。

### 问题2：约定邮箱收不到退订转发邮件 —— 阿里云自建服务器的关键点
你的应用部署在阿里云轻量应用服务器（属于阿里云公网出方向管控范围内），有以下三个alinux专属的排查点：

1. **阿里云默认封禁所有ECS/轻量应用服务器的出方向TCP 25端口**，这是策略级封禁，不是你能在轻量服务器"防火墙"面板里自己开的。
   好消息是你的 app.py 里 SMTP_PORT 默认已经是 465（SSL加密端口），465端口不受此限制，理论上不受影响 [web:11][web:21]。
   但务必确认你的环境变量 SMTP_PORT 没有被误设成 25。

2. **确认465端口的出方向连接确实能建立**，在服务器上直接用命令行测试（不依赖Python代码）：
   ```bash
   curl -v telnet://smtp.qiye.aliyun.com:465
   # 或
   timeout 5 bash -c "echo > /dev/tcp/smtp.qiye.aliyun.com/465" && echo "端口可连通" || echo "端口不可连通"
   ```
   如果提示连接超时/拒绝，说明轻量应用服务器控制台的【防火墙】规则里没有放行出方向流量（轻量服务器防火墙默认按"安全组"逻辑管理，建议登录控制台检查是否有出方向限制规则）。

3. **阿里企业邮箱SMTP需要"独立密码/客户端授权码"**，不是登录网页版邮箱的密码。在企业邮箱后台管理里单独获取，填入 SMTP_PASS 环境变量。

## 关于环境变量配置（alinux下用systemd管理，不是Render网页配置）
本次部署包用 systemd 服务（baai-unsubscribe.service）常驻运行你的Flask应用，环境变量直接写在
`/etc/systemd/system/baai-unsubscribe.service` 里的 Environment= 行，示例：
```
Environment="SMTP_HOST=smtp.qiye.aliyun.com"
Environment="SMTP_PORT=465"
Environment="SMTP_USER=你的发件邮箱@yourdomain.com"
Environment="SMTP_PASS=你的SMTP授权码"
Environment="NOTIFY_TO=你要接收退订通知的邮箱@yourdomain.com"
```
修改完环境变量后必须执行：
```bash
systemctl daemon-reload
systemctl restart baai-unsubscribe
```
否则新的环境变量不会生效（这是最容易被忽略的一步，很多人以为改了配置文件就自动生效）。

## 部署步骤（全新在alinux上部署）
1. 把整个压缩包解压后的所有文件上传到服务器 `/opt/baai-unsubscribe` 目录
   ```bash
   mkdir -p /opt/baai-unsubscribe
   # 用 scp 或宝塔/finalshell 等工具上传文件到该目录
   ```
2. 编辑 `baai-unsubscribe.service`，把里面的 SMTP_USER / SMTP_PASS / NOTIFY_TO 换成你的真实值
3. 执行一键部署脚本：
   ```bash
   cd /opt/baai-unsubscribe
   chmod +x deploy.sh
   sudo bash deploy.sh
   ```
4. 登录轻量应用服务器控制台，在【防火墙】里放行 80 端口（如果用了HTTPS还需放行443）的**入方向**规则
   （出方向的465端口通常默认放行，只有25端口被策略封禁，一般不需要手动放行出方向465）

## 部署完成后如何测试
1. 浏览器访问 `http://你的公网IP/health`，应返回 `{"status": "healthy"}`
2. 浏览器访问 `http://你的公网IP/api/test-email`，会返回详细的SMTP连接结果，例如：
   ```json
   {"success": false, "error": "(535, b'Error: authentication failed')", ...}
   ```
   如果 success 是 false，把 error 字段的具体内容记下来，基本都是密码/授权码错误或端口不通两类问题
3. 查看实时日志排查报错：
   ```bash
   journalctl -u baai-unsubscribe -f
   ```
4. 用真实退订链接走一遍完整流程，确认点击"确认退订"后页面显示"退订成功"

## 关于批量发信端 batch_mailer.py
没有改动。只需确认 config.yaml 里 unsubscribe.base_url 改成你alinux服务器的公网域名/IP，例如：
```
unsubscribe:
  enabled: true
  base_url: "http://你的公网IP或域名/unsubscribe"
  campaign_id: "CFTS_20260702"
```

## Token算法说明（不变）
app.py 里 generate_token() 和 batch_mailer.py 里 build_unsubscribe_link() 必须使用相同算法：
    token = md5(f"{email}:{campaign_id}")[:16]
