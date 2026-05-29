#!/usr/bin/env python3
"""
每日英语 Vlog 字幕抓取 (Bilibili) + Claude 分析 + 飞书推送
通过 Bilibili API 搜索英语 Vlog，提取字幕，生成学习报告推送到飞书群。

使用方法:
  python daily_vlog_fetch.py

环境变量:
  FEISHU_WEBHOOK_URL  飞书机器人 Webhook 地址（必填）
  ANTHROPIC_API_KEY   Claude API Key（可选，用于 AI 分析字幕）
"""

import os
import re
import sys
import json
import time
import random
import hashlib
import datetime
import functools
from pathlib import Path

import requests

# ============================================================
# 配置区
# ============================================================

BASE_DIR = Path(r"C:\Users\Tourism\Desktop\助理团队")
SUBTITLES_DIR = BASE_DIR / "subtitles"

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

VIDEO_COUNT = 4
MAX_SUBTITLE_CHARS = 3000

SEARCH_QUERIES = [
    "双语字幕 英语vlog",
    "英文字幕 vlog 日常",
    "全英vlog 双语字幕",
    "英语口语 vlog 字幕",
    "英文vlog 海外生活 字幕",
    "English vlog 双语",
    "英语学习 vlog 日常口语",
    "海外vlog 英语 字幕",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}

# Wbi 签名缓存
_wbi_keys = None
_wbi_lock_time = 0


# ============================================================
# Bilibili Wbi 签名
# ============================================================

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_mixin_key(orig_key: str) -> str:
    """对 imgKey 或 subKey 进行字符顺序打乱"""
    return "".join(orig_key[i] for i in MIXIN_KEY_ENC_TAB if i < len(orig_key))[:32]


def get_wbi_keys():
    """获取最新的 img_key 和 sub_key（缓存10分钟）"""
    global _wbi_keys, _wbi_lock_time
    now = time.time()
    if _wbi_keys and now - _wbi_lock_time < 600:
        return _wbi_keys

    # 请求导航栏信息以获取 wbi_img_url
    resp = requests.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers=HEADERS,
        timeout=10,
    )
    data = resp.json()
    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    # 从 URL 中提取 key（文件名去掉扩展名）
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0] if img_url else ""
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0] if sub_url else ""

    _wbi_keys = (img_key, sub_key)
    _wbi_lock_time = now
    return _wbi_keys


def sign_params(params: dict) -> dict:
    """为 B 站 API 参数添加 Wbi 签名"""
    img_key, sub_key = get_wbi_keys()
    if not img_key or not sub_key:
        return params

    mixin_key = get_mixin_key(img_key + sub_key)

    # 添加 wts（当前时间戳）
    params = dict(params)
    params["wts"] = int(time.time())

    # 按 key 排序
    sorted_params = sorted(params.items(), key=lambda x: x[0])

    # 构造查询字符串（不编码）
    query = "&".join(f"{k}={v}" for k, v in sorted_params)

    # 计算 MD5
    sign_str = query + mixin_key
    w_rid = hashlib.md5(sign_str.encode()).hexdigest()

    params["w_rid"] = w_rid
    return params


@functools.lru_cache(maxsize=128)
def bili_api_get(url: str, params_json: str):
    """带 Wbi 签名的 B 站 API GET 请求（params_json 用于缓存）"""
    params = json.loads(params_json)
    signed_params = sign_params(params)
    try:
        resp = requests.get(url, params=signed_params, headers=HEADERS, timeout=15)
        return resp.json()
    except Exception:
        return None


def search_bilibili(keyword: str, count: int = 8):
    """搜索 B 站视频 → [(title, bvid, aid, author, duration), ...]"""
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": 1,
        "order": "pubdate",
    }
    data = bili_api_get(
        "https://api.bilibili.com/x/web-interface/wbi/search/type",
        json.dumps(params, sort_keys=True),
    )

    if not data or data.get("code") != 0:
        # Fallback: try non-wbi search
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/search/type",
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        data = resp.json()

    if not data or data.get("code") != 0:
        return []

    videos = []
    results = data.get("data", {}).get("result", [])
    for item in results:
        title = re.sub(r"<.*?>", "", item.get("title", ""))
        bvid = item.get("bvid", "")
        aid = item.get("aid", 0)
        author = item.get("author", "B站UP主")
        duration = item.get("duration", "?")
        if isinstance(duration, (int, float)):
            m, s = divmod(int(duration), 60)
            duration = f"{m}:{s:02d}"
        videos.append((title, bvid, aid, author, duration))
        if len(videos) >= count:
            break

    return videos


def get_video_info(bvid: str = None, aid: int = None):
    """获取视频详细信息"""
    if bvid:
        params = {"bvid": bvid}
    elif aid:
        params = {"aid": aid}
    else:
        return None

    data = bili_api_get(
        "https://api.bilibili.com/x/web-interface/view",
        json.dumps(params, sort_keys=True),
    )
    if not data or data.get("code") != 0:
        return None

    info = data["data"]
    return {
        "title": info.get("title", ""),
        "bvid": info.get("bvid", ""),
        "aid": info.get("aid", 0),
        "cid": info.get("cid", 0),
        "duration": info.get("duration", 0),
        "owner": info.get("owner", {}).get("name", "B站UP主"),
        "pages": info.get("pages", []),
    }


def get_subtitle_info(aid: int, cid: int):
    """获取字幕 URL 列表"""
    params = {"aid": aid, "cid": cid}
    data = bili_api_get(
        "https://api.bilibili.com/x/player/v2",
        json.dumps(params, sort_keys=True),
    )

    if not data or data.get("code") != 0:
        # Fallback
        resp = requests.get(
            "https://api.bilibili.com/x/player/v2",
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        data = resp.json()

    subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
    return subtitles


def download_subtitle(subtitle_url: str):
    """下载并解析 B 站 JSON 字幕"""
    if not subtitle_url:
        return None
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url

    try:
        resp = requests.get(subtitle_url, headers=HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return None

    lines = []
    body = data.get("body", data) if isinstance(data, dict) else data
    if isinstance(body, list):
        for item in body:
            content = item.get("content", "").strip()
            if content:
                lines.append(content)

    return "\n".join(lines)


# ============================================================
# 字幕清洗
# ============================================================

def clean_subtitle_text(text: str) -> str:
    """清洗字幕，保留英文内容"""
    if not text:
        return ""

    lines = text.split("\n")
    en_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 计算英文字符占比（过滤纯中文行）
        alpha_count = sum(1 for c in line if c.isascii() and c.isalpha())
        total = max(len(line.replace(" ", "")), 1)
        if total > 0 and alpha_count / total > 0.5:
            en_lines.append(line)

    # 去重复连续行
    cleaned = []
    for line in en_lines:
        if cleaned and line == cleaned[-1]:
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


# ============================================================
# Claude AI 分析
# ============================================================

def analyze_with_claude(video_title: str, author: str, subtitle_text: str) -> str:
    """使用 Claude API 分析字幕"""
    if not ANTHROPIC_KEY:
        return basic_analysis(subtitle_text)

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_KEY)
        text_sample = subtitle_text[:MAX_SUBTITLE_CHARS]

        prompt = f"""Analyze this English vlog subtitle and extract learning material.

Video: "{video_title}" by {author}

Subtitle:
---
{text_sample}
---

Output in this EXACT format:

## 核心词汇
| 词汇/短语 | 中文释义 | 原文例句 | 话题分类 |
|-----------|----------|----------|----------|
| (8-10 B1+ level items from the text) |

## 地道句型
- (2-3 sentences worth memorizing, with Chinese translation)

## 话题概要
(One sentence in Chinese summarizing the topic)"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"       Claude API error: {e}")
        return basic_analysis(subtitle_text)


def basic_analysis(subtitle_text: str) -> str:
    """基础词频分析（无需 API）"""
    words = re.findall(r"\b[a-zA-Z]{4,}\b", subtitle_text.lower())

    stopwords = {
        "this", "that", "with", "have", "from", "they", "will", "what",
        "when", "where", "which", "about", "your", "just", "like", "been",
        "would", "could", "should", "there", "their", "really", "going",
        "very", "much", "some", "then", "than", "also", "into", "over",
        "know", "yeah", "well", "right", "back", "more", "because",
        "here", "were", "them", "said", "people", "think", "thing",
        "don", "didn", "wasn", "isn", "hadn", "hasn", "won", "can",
    }

    word_freq = {}
    for w in words:
        if w not in stopwords:
            word_freq[w] = word_freq.get(w, 0) + 1

    top = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:12]

    lines = ["## 高频词汇（词频统计）", ""]
    lines.append("| 单词 | 出现次数 |")
    lines.append("|------|----------|")
    for word, freq in top:
        lines.append(f"| {word} | {freq} |")
    lines.append("")
    lines.append("> 设置 ANTHROPIC_API_KEY 环境变量可启用 AI 智能提取。")
    return "\n".join(lines)


# ============================================================
# 飞书推送
# ============================================================

def send_to_feishu(today_str: str, video_reports: list) -> bool:
    """发送每日报告到飞书群"""
    if not FEISHU_WEBHOOK:
        print("\n[WARN] FEISHU_WEBHOOK_URL 未设置，跳过飞书推送")
        return False

    sections = [f"📺 **每日英语 Vlog 学习报告 · {today_str}**\n"]
    sections.append(f"今日从 B 站抓取 {len(video_reports)} 个英语 Vlog\n")

    total_words = 0
    for i, r in enumerate(video_reports, 1):
        title = r['title'][:80]
        sections.append(f"---\n**{i}. [{title}](https://www.bilibili.com/video/{r['bvid']})**")
        sections.append(f"👤 UP主：{r['author']}  ⏱️ {r['duration']}")
        sections.append("")
        sections.append(r['analysis'])
        sections.append("")
        total_words += r.get('word_count', 0)

    sections.append(f"---\n📊 **今日统计**：{len(video_reports)} 个视频 | 预估词汇 ~{total_words} 个")

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"每日英语Vlog · {today_str}"},
                "template": "blue",
            },
            "elements": [{"tag": "markdown", "content": "\n".join(sections)}],
        },
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("code") == 0:
            print("   Feishu: Sent OK")
            return True
        print(f"   Feishu error: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"   Feishu error: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def process_video(title: str, bvid: str, aid: int, author: str, duration: str):
    """处理单个视频：获取字幕 → 清洗 → 分析"""
    info = get_video_info(bvid=bvid, aid=aid)
    if not info:
        return None

    cid = info["cid"]
    aid_val = info["aid"]

    subtitles = get_subtitle_info(aid_val, cid)
    if not subtitles:
        return None

    # 优先英语字幕
    en_url = None
    for sub in subtitles:
        lang = sub.get("lan_doc", "").lower()
        if "en" in lang or "eng" in lang:
            en_url = sub.get("subtitle_url", "")
            break

    # Fallback: 根据标题语言判断（可能是中英双语字幕）
    if not en_url:
        for sub in subtitles:
            lang = sub.get("lan_doc", "")
            if "中文" in lang or "汉语" in lang or "zh" in lang.lower():
                en_url = sub.get("subtitle_url", "")
                break

    # Last resort: 取第一个
    if not en_url and subtitles:
        en_url = subtitles[0].get("subtitle_url", "")

    if not en_url:
        return None

    # 下载字幕
    raw_text = download_subtitle(en_url)
    if not raw_text:
        return None

    # 清洗
    text = clean_subtitle_text(raw_text)
    if not text or len(text.split()) < 20:
        return None

    word_count = len(text.split())
    analysis = analyze_with_claude(title, author, text)

    return {
        "title": title,
        "bvid": bvid,
        "author": author,
        "duration": duration,
        "analysis": analysis,
        "word_count": min(word_count // 10, 10),
    }


def main():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  每日英语Vlog抓取 (Bilibili) — {today_str}")
    print(f"{'='*60}\n")

    today_dir = SUBTITLES_DIR / today_str
    today_dir.mkdir(parents=True, exist_ok=True)

    # 1. 搜索
    query = random.choice(SEARCH_QUERIES)
    print(f"[1/3] 搜索 B 站: '{query}'")
    videos = search_bilibili(query, VIDEO_COUNT * 3)
    print(f"      找到 {len(videos)} 个候选视频\n")

    if not videos:
        print("ERROR: 搜索无结果。")
        return 1

    # 2. 逐个处理
    video_reports = []
    for i, (title, bvid, aid, author, duration) in enumerate(videos):
        if len(video_reports) >= VIDEO_COUNT:
            break

        print(f"[2/3] [{len(video_reports)+1}/{VIDEO_COUNT}] {title[:65]}")
        print(f"      UP主: {author}  时长: {duration}")

        report = process_video(title, bvid, aid, author, duration)
        if report:
            video_reports.append(report)
            print(f"      字幕: {report['word_count']*10} 词 → OK\n")
        else:
            print(f"      无英文字幕/解析失败 → SKIP\n")

        time.sleep(0.5)  # 避免触发限流

    # 3. 推送 + 保存
    print(f"[3/3] 推送报告...")
    success = send_to_feishu(today_str, video_reports)

    report_path = today_dir / f"report_{today_str}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 每日英语Vlog学习报告 — {today_str}\n\n")
        f.write(f"**平台**: Bilibili  |  **搜索关键词**: {query}\n\n")
        for i, r in enumerate(video_reports, 1):
            f.write(f"## {i}. {r['title']}\n\n")
            f.write(f"- **UP主**: {r['author']}\n")
            f.write(f"- **链接**: https://www.bilibili.com/video/{r['bvid']}\n")
            f.write(f"- **时长**: {r['duration']}\n\n")
            f.write(r['analysis'])
            f.write("\n\n---\n\n")

    print(f"\n{'='*60}")
    print(f"  摘要")
    print(f"{'='*60}")
    print(f"  平台            : Bilibili")
    print(f"  成功/候选       : {len(video_reports)}/{len(videos)}")
    print(f"  飞书推送        : {'OK' if success else 'SKIP'}")
    print(f"  本地报告        : {report_path}")
    print(f"{'='*60}\n")

    return 0 if video_reports else 1


if __name__ == "__main__":
    sys.exit(main())
