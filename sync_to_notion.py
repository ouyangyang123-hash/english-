#!/usr/bin/env python3
"""
同步每日英语Vlog报告到 Notion 词汇银行
读取本地报告 → 解析词汇 → 写入 Notion 数据库

使用方法:
  python sync_to_notion.py [日期YYYY-MM-DD]
  python sync_to_notion.py 2026-05-28
"""

import os
import re
import sys
import json
import datetime
from pathlib import Path

import requests

# ============================================================
# 配置
# ============================================================

NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "36e9fe6dced181298587dbef7bb13139")
BASE_DIR = Path(r"C:\Users\Tourism\Desktop\助理团队")
REPORTS_DIR = BASE_DIR / "vlog文稿"

HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def parse_vocabulary_from_report(report_text):
    """从报告 Markdown 中解析词汇表"""
    words = []

    # 匹配表格行：| word | 中文释义 | 例句 |
    table_pattern = re.compile(
        r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|"
    )

    in_table = False
    for line in report_text.split("\n"):
        line = line.strip()
        if "| 词汇" in line and "| 中文" in line:
            in_table = True
            continue
        if "|---" in line:
            continue
        if in_table and line.startswith("|") and line.endswith("|"):
            # 跳过空行和非数据行
            if "词汇/短语" in line or "---" in line:
                continue
            match = table_pattern.match(line)
            if match:
                word = match.group(1).strip()
                meaning = match.group(2).strip()
                example = match.group(3).strip()
                # 跳过明显不是词汇的行
                if word and len(word) > 1 and not word.startswith("**"):
                    words.append((word, meaning, example))

        # 表格结束
        if in_table and not line.startswith("|"):
            in_table = False

    return words


def extract_topic_from_text(text):
    """从文本推断话题分类"""
    topics = {
        "Travel": ["travel", "trip", "flight", "hotel", "airport", "旅行", "机场", "酒店"],
        "Food": ["food", "eat", "restaurant", "cook", "recipe", "食物", "餐厅", "烹饪"],
        "Daily Life": ["daily", "routine", "morning", "life", "house", "日常", "生活"],
        "Work": ["work", "job", "office", "meeting", "career", "工作", "会议", "职场"],
        "Tech": ["tech", "phone", "computer", "app", "digital", "科技", "手机", "电脑"],
        "Social": ["friend", "party", "chat", "social", "talk", "朋友", "社交", "聚会"],
        "Health": ["health", "exercise", "workout", "fitness", "健康", "运动", "健身"],
        "Emotions": ["feel", "happy", "sad", "excited", "emotion", "感觉", "情绪"],
    }

    text_lower = text.lower()
    for topic, keywords in topics.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return topic
    return "Daily Life"


def extract_pos(word):
    """推断词性"""
    word = word.strip()
    if " " in word or "of" in word or "the" in word:
        return "phrasal verb" if any(v in word for v in ["up", "down", "out", "in", "on", "off", "away"]) else "expression"
    if word.endswith("ing"):
        return "v."
    if word.endswith("ly"):
        return "adv."
    if word.endswith("ed") and len(word) > 5:
        return "adj."
    return "n."


def add_to_notion(word, meaning, example, topic, pos, date_str):
    """添加一条词汇到 Notion 数据库"""
    page_data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "单词/短语": {"title": [{"text": {"content": word}}]},
            "中文释义": {"rich_text": [{"text": {"content": meaning}}]},
            "原文例句": {"rich_text": [{"text": {"content": example}}]},
            "话题分类": {"select": {"name": topic}},
            "词性": {"select": {"name": pos}},
            "掌握状态": {"select": {"name": "新学"}},
            "来源日期": {"date": {"start": date_str}},
            "复习次数": {"number": 0},
            "简易度EF": {"number": 2.5},
            "间隔天数": {"number": 1},
        },
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json=page_data,
        timeout=15,
    )
    return resp.status_code == 200


def sync_report(date_str):
    """同步指定日期的报告到 Notion"""
    report_dir = REPORTS_DIR / date_str
    report_file = report_dir / f"report_{date_str}.md"

    if not report_file.exists():
        print(f"Report not found: {report_file}")
        return 0

    report_text = report_file.read_text(encoding="utf-8")

    # 解析词汇
    words = parse_vocabulary_from_report(report_text)

    if not words:
        print(f"No vocabulary found in report for {date_str}")
        return 0

    # 逐条写入 Notion
    added = 0
    for word, meaning, example in words:
        topic = extract_topic_from_text(example)
        pos = extract_pos(word)

        if add_to_notion(word, meaning, example, topic, pos, date_str):
            added += 1
            print(f"  OK  {word}  [{topic}] {meaning}")
        else:
            print(f"  FAIL {word}")

        # 避免触发 Notion 限流（每秒3次）
        import time
        time.sleep(0.35)

    return added


def main():
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        # 默认今天
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    print(f"\nNotion Sync — {date_str}")
    print(f"{'='*50}")

    added = sync_report(date_str)

    print(f"\n{'='*50}")
    print(f"Synced: {added} words to Notion")
    print(f"Notion: https://www.notion.so/36e9fe6dced181298587dbef7bb13139")
    print()

    return 0 if added > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
