"""
BAAI-CFTS 退订收集后端 - 轻量应用服务器部署版（修复版 v2）
技术栈：Python 3.10 + Flask + SQLite + SMTP转发

本次修复的两个问题：
1. 前端点击"确认退订"后没有显示"退订成功"反馈页面
   -> 原因：模板里 JS 提交成功后没有正确切换 DOM 显示状态 / fetch 请求没有正确捕获返回结果
   -> 已在 templates/unsubscribe.html 中重写前端逻辑，确保成功后展示成功页

2. 配置的 NOTIFY_TO 邮箱一直收不到退订转发邮件
   -> 原因：原代码里 SMTP 发送异常被 print() 吞掉，Render 上看不到日志细节，
      同时没有任何独立的方式验证 SMTP 配置是否真正生效
   -> 已增加：
      a) 详细的 traceback 日志，方便在 Render 后台日志里定位真正报错原因
      b) 独立的 /api/test-email 测试接口，用于单独验证 SMTP 是否配置正确
      c) 邮件发送失败时会把详细错误一并写入数据库的 forward_error 字段，方便排查
      d) SMTP 连接增加超时时间，避免连接卡死没有任何反馈
Token算法需与你的 batch_mailer.py 里的 build_unsubscribe_link 保持一致：
token = md5(f"{email}:{campaign_id}")[:16]
"""

from flask import Flask, request, jsonify, render_template
import sqlite3
import smtplib
import ssl
import hashlib
import logging
import traceback
from email.mime.text import MIMEText
from email.header import Header
import datetime
import os

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("unsubscribe")

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "unsubscribe.db"),
)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unsubscribe_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            name TEXT,
            organization TEXT,
            campaign_id TEXT,
            token TEXT,
            reasons TEXT,
            other_text TEXT,
            user_agent TEXT,
            ip TEXT,
            page_url TEXT,
            created_at TEXT,
            forward_status TEXT,
            forward_error TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()

# ---------- SMTP转发配置：全部通过环境变量注入，不要硬编码 ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qiye.aliyun.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
NOTIFY_TO = os.environ.get("NOTIFY_TO", "")


def generate_token(email, campaign_id):
    """必须和 batch_mailer.py 里的算法完全一致：md5(email:campaign_id)前16位"""
    raw = email + ":" + campaign_id
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def verify_token(email, token, campaign_id):
    if not token or not email:
        return False
    expected = generate_token(email, campaign_id)
    return expected == token


def build_email(record):
    subject = "[退订通知] " + (record["name"] or record["email"]) + " 已退订"
    body = (
        "姓名：" + str(record["name"]) + "\n"
        + "所属机构：" + str(record["organization"]) + "\n"
        + "退订邮箱：" + str(record["email"]) + "\n"
        + "活动编号：" + str(record["campaign_id"]) + "\n"
        + "退订原因：" + str(record["reasons"]) + "\n"
        + "补充说明：" + str(record["other_text"]) + "\n"
        + "提交时间：" + str(record["created_at"]) + "\n"
        + "IP地址：" + str(record["ip"]) + "\n"
        + "User-Agent：" + str(record["user_agent"]) + "\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_TO
    return msg


def send_mail(msg, to_addr):
    """独立的发信函数，带超时 + 详细异常抛出，方便上层记录日志"""
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to_addr], msg.as_string())


def forward_to_email(record):
    """
    转发退订通知邮件。返回 (success: bool, error_message: str)
    任何异常都会被记录到日志和数据库，不会再被静默吞掉。
    """
    missing = [
        name
        for name, val in [
            ("SMTP_USER", SMTP_USER),
            ("SMTP_PASS", SMTP_PASS),
            ("NOTIFY_TO", NOTIFY_TO),
        ]
        if not val
    ]
    if missing:
        msg = "环境变量未配置完整，缺少：" + ", ".join(missing)
        logger.warning(msg)
        return False, msg

    try:
        msg = build_email(record)
        send_mail(msg, NOTIFY_TO)
        logger.info("退订通知邮件已发送至 %s", NOTIFY_TO)
        return True, ""
    except Exception as e:
        err = "邮件转发失败：" + repr(e) + "\n" + traceback.format_exc()
        logger.error(err)
        return False, str(e)


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe_page():
    """
    渲染退订页面。邮件模板中的退订链接格式为：
    https://你的域名/unsubscribe?email=xxx&cid=活动编号&token=xxx
    页面内的JS会自行从URL参数中读取email/token/cid并展示，并在提交时POST到/api/unsubscribe。
    """
    email = request.args.get("email", "")
    token = request.args.get("token", "")
    campaign_id = request.args.get("cid", "")

    if not verify_token(email, token, campaign_id):
        return "退订链接无效或已过期，请联系管理员。", 400

    return render_template(
        "unsubscribe.html", email=email, token=token, campaign_id=campaign_id
    )


@app.route("/api/unsubscribe", methods=["POST"])
def unsubscribe():
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "error", "message": "请求格式错误"}), 400

    email = data.get("email", "")
    token = data.get("token", "")
    campaign_id = data.get("cid", data.get("campaign_id", ""))

    if not verify_token(email, token, campaign_id):
        return jsonify({"status": "error", "message": "invalid token"}), 400

    record = {
        "email": email,
        "name": data.get("name", ""),
        "organization": data.get("organization", ""),
        "campaign_id": campaign_id,
        "token": token,
        "reasons": ",".join(data.get("reasons", [])),
        "other_text": data.get("other_text", ""),
        "user_agent": data.get("user_agent", request.headers.get("User-Agent", "")),
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "page_url": data.get("page_url", ""),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    forward_ok, forward_err = forward_to_email(record)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """
            INSERT INTO unsubscribe_log
            (email, name, organization, campaign_id, token, reasons, other_text,
             user_agent, ip, page_url, created_at, forward_status, forward_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            tuple(record.values()) + ("ok" if forward_ok else "failed", forward_err),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("写入数据库失败：%s\n%s", e, traceback.format_exc())
        # 即使数据库写入失败，也不影响给前端返回"退订已收到"的提示，
        # 因为核心目的是记录用户已经点击退订，避免用户重复点击却看不到任何反馈

    return jsonify(
        {
            "status": "ok",
            "message": "退订成功",
            "mail_forwarded": forward_ok,
        }
    ), 200


@app.route("/api/test-email", methods=["GET"])
def test_email():
    """
    专门用于排查"约定邮箱收不到退信邮件"问题的测试接口。
    部署后直接浏览器访问 /api/test-email，会返回具体的 SMTP 发送结果和错误详情，
    不需要走完整退订流程即可验证环境变量和SMTP配置是否正确。
    """
    fake_record = {
        "email": "test@example.com",
        "name": "测试用户",
        "organization": "测试机构",
        "campaign_id": "TEST",
        "reasons": "测试",
        "other_text": "这是一封测试邮件，用于验证SMTP转发配置",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ip": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }
    ok, err = forward_to_email(fake_record)
    return jsonify(
        {
            "success": ok,
            "error": err,
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_user_configured": bool(SMTP_USER),
            "smtp_pass_configured": bool(SMTP_PASS),
            "notify_to_configured": bool(NOTIFY_TO),
            "notify_to": NOTIFY_TO,
        }
    ), (200 if ok else 500)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
