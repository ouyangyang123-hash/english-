#!/usr/bin/env python3
"""
飞书消息发送工具 - 被 Claude Code cron 调用
将每日英语学习报告推送到飞书用户
"""
import requests
import json
import sys
import os

# 飞书应用凭证
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
OPEN_ID = os.environ.get("FEISHU_OPEN_ID", "")

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"


def get_token():
    resp = requests.post(TOKEN_URL, json={
        "app_id": APP_ID, "app_secret": APP_SECRET
    }, timeout=15)
    return resp.json()["tenant_access_token"]


def send_card(title, markdown_content):
    """发送飞书卡片消息"""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "receive_id": OPEN_ID,
        "msg_type": "interactive",
        "content": json.dumps({
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": markdown_content}
            ]
        })
    }

    resp = requests.post(MSG_URL, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") == 0:
        print("Feishu: sent OK")
        return True
    else:
        print(f"Feishu error: {result}")
        return False


def send_text(text):
    """发送飞书文本消息"""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    payload = {
        "receive_id": OPEN_ID,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }

    resp = requests.post(MSG_URL, headers=headers, json=payload, timeout=15)
    result = resp.json()
    return result.get("code") == 0


if __name__ == "__main__":
    # 从 stdin 或参数读取内容
    if len(sys.argv) > 1:
        title = sys.argv[1]
        content = sys.argv[2] if len(sys.argv) > 2 else ""
    else:
        title = "英语学习通知"
        content = sys.stdin.read().strip()

    if len(content) > 5000:
        content = content[:5000] + "\n\n...[内容过长，已截断]"

    send_card(title, content)
