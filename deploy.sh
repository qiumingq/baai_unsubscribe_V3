#!/bin/bash
# BAAI-CFTS 退订系统 一键部署脚本（Alibaba Cloud Linux / alinux 专用）
# 用法：将本项目文件全部上传到 /home/admin/baai_unsubscribe_V3 后，在该目录下执行：
#   sudo bash deploy.sh

set -e

APP_DIR="/home/admin/baai_unsubscribe_V3"
cd "$APP_DIR"

echo "[1/6] 安装系统依赖（python3, pip, nginx）..."
yum install -y python3 python3-pip nginx

echo "[2/6] 创建虚拟环境..."
python3 -m venv venv
source venv/bin/activate

echo "[3/6] 安装Python依赖..."
pip install --upgrade pip
pip install -r requirements.txt

echo "[4/6] 配置 systemd 服务..."
cp baai-unsubscribe.service /etc/systemd/system/baai-unsubscribe.service
systemctl daemon-reload
systemctl enable baai-unsubscribe
systemctl restart baai-unsubscribe

echo "[5/6] 配置 nginx 反向代理..."
cp nginx_baai-unsubscribe.conf /etc/nginx/conf.d/baai-unsubscribe.conf
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "[6/6] 检查防火墙（firewalld）放行80端口..."
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=http
  firewall-cmd --reload
fi

echo "部署完成。请到轻量应用服务器控制台的【防火墙】里额外放行 80/443 端口的入方向规则。"
echo "然后访问 http://你的公网IP/health 应返回 {\"status\": \"healthy\"}"
