#!/usr/bin/env python3
"""
GitHub Actions 每日英语Vlog报告生成器
流程:
1. yt-dlp 搜索 YouTube 英语Vlog
2. 下载英文字幕 (auto-generated)
3. DeepSeek API 提取词汇/句型
4. 生成 Markdown 报告
5. 同步到 Notion 词汇银行
6. 推送到飞书

GitHub Actions 定时: 每天 22:00 UTC = 北京时间 6:00 AM
"""

import os
import sys
import re
import json
import subprocess
import datetime
import time
import shutil
import tempfile
from pathlib import Path

import requests

# ============================================================
# 配置 (敏感信息通过 GitHub Secrets 传入)
# ============================================================

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_OPEN_ID = os.environ.get("FEISHU_OPEN_ID", "")
NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DB = os.environ.get("NOTION_DATABASE_ID", "")
YOUTUBE_COOKIES = os.environ.get("YOUTUBE_COOKIES", "")

TODAY = datetime.date.today().strftime("%Y-%m-%d")
IS_CI = os.environ.get("GITHUB_ACTIONS", "") == "true"

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "vlog文稿" / TODAY
SUBTITLE_DIR = REPO_ROOT / "subtitles"

# yt-dlp 基础参数
YTDLP_BASE = [
    "yt-dlp",
    "--socket-timeout", "30",
    "--extractor-retries", "3",
    "--fragment-retries", "3",
]


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    print(f"  [{datetime.datetime.now():%H:%M:%S}] {msg}")


def get_cookie_args():
    """如果有 YouTube cookies 则返回 --cookies 参数"""
    if not YOUTUBE_COOKIES:
        return []
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='yt_cookies_')
    f.write(YOUTUBE_COOKIES)
    f.close()
    return ["--cookies", f.name]


# ============================================================
# Step 1: YouTube 搜索
# ============================================================

def search_videos(query, count=4):
    """yt-dlp 搜索 YouTube 视频，返回视频信息列表"""
    cmd = YTDLP_BASE + [
        f"ytsearch{count}:{query}",
        "--dump-json", "--no-download", "--flat-playlist",
    ] + get_cookie_args()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            log(f"yt-dlp search failed (rc={result.returncode})")
            if result.stderr:
                log(f"  stderr: {result.stderr[:300]}")
            return []

        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            dur = info.get("duration", 0) or 0
            # duration 为 0 表示未知（flat-playlist），不过滤
            if dur > 0 and (dur < 120 or dur > 3600):
                continue
            videos.append({
                "id": info.get("id", ""),
                "title": info.get("title", ""),
                "channel": info.get("channel", info.get("uploader", "Unknown")),
                "url": info.get("webpage_url", f"https://youtube.com/watch?v={info.get('id', '')}"),
                "duration": dur,
            })
        return videos
    except subprocess.TimeoutExpired:
        log(f"Timeout searching YouTube for '{query}'")
        return []
    except Exception as e:
        log(f"YouTube search error: {e}")
        return []


def download_subtitle(video_id):
    """下载单个视频的英文字幕，返回清洗后的文本"""
    prefix = SUBTITLE_DIR / video_id

    cmd = YTDLP_BASE + [
        f"https://youtube.com/watch?v={video_id}",
        "--write-auto-subs", "--sub-lang", "en",
        "--skip-download", "--convert-subs", "srt",
        "-o", str(prefix),
    ] + get_cookie_args()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"yt-dlp download failed (rc={result.returncode})")
            if result.stderr:
                log(f"  stderr: {result.stderr[:300]}")

        for ext in [".en.srt", ".srt", ".en.vtt", ".vtt"]:
            f = Path(str(prefix) + ext)
            if f.exists():
                text = f.read_text(encoding="utf-8", errors="ignore")
                if len(text.strip()) > 50:
                    return clean_subtitle(text)
        return ""
    except Exception as e:
        log(f"Download error for {video_id}: {e}")
        return ""


def clean_subtitle(text):
    """清洗 SRT/VTT 字幕文本"""
    text = re.sub(r'^WEBVTT.*?\n\n', '', text, flags=re.DOTALL)
    text = re.sub(r'\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}', '', text)
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'align:\w+|position:\d+%|line:\d+%|size:\d+%', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return '\n'.join(lines)


# ============================================================
# Step 2: DeepSeek API 分析
# ============================================================

def analyze_subtitle(text, video):
    """调用 DeepSeek API 从字幕提取词汇和句型"""
    if not DEEPSEEK_API_KEY:
        log("WARNING: No DEEPSEEK_API_KEY set, using fallback")
        return fallback_extraction(text)

    max_chars = 6000
    snippet = text if len(text) <= max_chars else text[:max_chars] + "\n...[truncated]"

    system_prompt = (
        "You are an English teacher for Chinese learners. "
        "Extract practical expressions from vlog subtitles. "
        "Focus on B1+ level. Keep Chinese meanings short. "
        "Reply in EXACTLY the format specified."
    )

    user_prompt = f"""Analyze these English vlog subtitles for a Chinese English learner (B1-B2 level).

Video title: {video['title']}
Channel: {video['channel']}

Subtitles:
---
{snippet}
---

Extract:
1. **8-10 useful expressions** at B1+ level (phrasal verbs, idioms, collocations, everyday phrases). Skip basic words like "hello", "thank you".
2. For each: the expression, a SHORT Chinese meaning (≤8 Chinese characters), and the EXACT sentence from the subtitles.
3. **2-3 sentence patterns** worth memorizing — complete sentences with Chinese translation.

Reply in EXACTLY this format (do not deviate):

TOPIC: [one topic from: Daily Life / Work / Social / Travel / Food / Tech / Education / Shopping / Emotions / Health]

VOCABULARY:
| 词汇/短语 | 中文释义 | 原文例句 |
|-----------|----------|----------|
| expression1 | 中文意思 | exact sentence from subtitles |
| expression2 | 中文意思 | exact sentence from subtitles |

PATTERNS:
- "English sentence" → 中文翻译
- "English sentence" → 中文翻译"""

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "temperature": 0.3,
                "max_tokens": 2000,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=60,
        )
        data = resp.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        else:
            log(f"DeepSeek API error: {data}")
            return fallback_extraction(text)
    except Exception as e:
        log(f"DeepSeek API error: {e}")
        return fallback_extraction(text)


def fallback_extraction(text):
    """无 API 时的备用提取"""
    lines = [l.strip() for l in text.split('\n') if 20 < len(l.strip()) < 200]
    patterns = '\n'.join(f'- "{l}" → (待翻译)' for l in lines[:3])

    return f"""TOPIC: Daily Life

VOCABULARY:
| 词汇/短语 | 中文释义 | 原文例句 |
|-----------|----------|----------|
| (需要 DeepSeek API Key) | 请配置密钥 | Please configure DEEPSEEK_API_KEY in GitHub Secrets |

PATTERNS:
{patterns if patterns else '- "No patterns available" → 无'}
"""


# ============================================================
# Step 3: 报告生成
# ============================================================

def generate_report(results):
    """根据分析结果生成完整报告"""
    sections = []
    topics = []
    total_words = 0
    total_patterns = 0

    for i, (video, analysis) in enumerate(results, 1):
        if not analysis:
            continue

        topic_m = re.search(r'TOPIC:\s*(.+)', analysis)
        topic = topic_m.group(1).strip() if topic_m else "Daily Life"
        topics.append(topic)

        vocab = ""
        vm = re.search(r'VOCABULARY:\s*\n((?:\|.+\|\n?)+)', analysis)
        if vm:
            vocab = vm.group(1).strip()
            total_words += max(0, len(re.findall(r'^\|.*\|.*\|.*\|', vocab, re.MULTILINE)) - 1)

        patterns = ""
        pm = re.search(r'PATTERNS:\s*\n((?:.+\n?)+)', analysis)
        if pm:
            patterns = pm.group(1).strip()
            total_patterns += len(re.findall(r'^\s*["-]', patterns, re.MULTILINE))

        sections.append(f"""## {i}. {video['title']}
🔗 来源: YouTube「{video['channel']}」
🔗 链接: {video['url']}
📂 话题：{topic}

### 词汇表
{vocab if vocab else '_(无词汇)_'}

### 地道句型
{patterns if patterns else '_(无句型)_'}
""")

    unique_topics = list(dict.fromkeys(topics))
    NL = '\n'
    SEP = NL + '---' + NL + NL

    report = f"""# 每日英语Vlog学习报告 · {TODAY}

{SEP.join(sections)}

---
## 今日统计
- 共分析 {len(sections)} 个视频
- 核心词汇 {total_words} 个
- 地道句型 {total_patterns} 个
- 覆盖话题: {' / '.join(unique_topics) if unique_topics else 'Daily Life'}
"""
    return report


# ============================================================
# Step 4: Notion 同步
# ============================================================

def sync_notion(date_str, report_text):
    """解析报告中的词汇表并写入 Notion 数据库"""
    if not NOTION_KEY or not NOTION_DB:
        log("Notion: skipping (no API key or database ID)")
        return 0

    headers = {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    row_pat = re.compile(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|")
    words = []

    for line in report_text.split("\n"):
        m = row_pat.match(line.strip())
        if not m:
            continue
        w = m.group(1).strip()
        if w in ("词汇/短语",) or w.startswith("---") or w.startswith("**"):
            continue
        if len(w) > 1:
            words.append((w, m.group(2).strip(), m.group(3).strip()))

    if not words:
        log("No vocabulary found in report")
        return 0

    added = 0
    for word, meaning, example in words:
        if " " in word:
            pos = "expression"
        elif word.endswith("ing"):
            pos = "v."
        elif word.endswith("ly"):
            pos = "adv."
        else:
            pos = "n."

        page = {
            "parent": {"database_id": NOTION_DB},
            "properties": {
                "单词/短语": {"title": [{"text": {"content": word}}]},
                "中文释义": {"rich_text": [{"text": {"content": meaning}}]},
                "原文例句": {"rich_text": [{"text": {"content": example}}]},
                "话题分类": {"select": {"name": "Daily Life"}},
                "词性": {"select": {"name": pos}},
                "掌握状态": {"select": {"name": "新学"}},
                "来源日期": {"date": {"start": date_str}},
                "复习次数": {"number": 0},
                "简易度EF": {"number": 2.5},
                "间隔天数": {"number": 1},
            },
        }

        try:
            r = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page, timeout=15)
            if r.status_code == 200:
                added += 1
            else:
                log(f"Notion fail [{word}]: {r.status_code} {r.text[:100]}")
        except Exception as e:
            log(f"Notion error [{word}]: {e}")

        time.sleep(0.35)

    log(f"Notion: {added} words synced")
    return added


# ============================================================
# Step 5: 飞书推送
# ============================================================

def send_feishu(report_text):
    """推送到飞书用户"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        log("Feishu: skipping (no credentials)")
        return False

    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=15,
    )

    if r.status_code != 200:
        log(f"Feishu auth HTTP error: {r.status_code} {r.text[:200]}")
        return False

    auth_data = r.json()
    if auth_data.get("code") != 0:
        log(f"Feishu auth failed: {auth_data.get('msg', auth_data)}")
        return False

    token = auth_data.get("tenant_access_token", "")
    if not token:
        log("Feishu auth: no token returned")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    content = report_text
    if len(content) > 4800:
        content = content[:4800] + "\n\n...(truncated)"

    payload = {
        "receive_id": FEISHU_OPEN_ID,
        "msg_type": "interactive",
        "content": json.dumps({
            "header": {
                "title": {"tag": "plain_text", "content": f"每日英语Vlog学习 · {TODAY}"},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": content}
            ],
        }),
    }

    r = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        headers=headers, json=payload, timeout=15,
    )
    result = r.json()
    if result.get("code") == 0:
        log("Feishu: sent OK")
        return True
    else:
        log(f"Feishu error: {result}")
        return False


# ============================================================
# 主流程
# ============================================================

def main():
    print(f"\n{'='*55}")
    print(f"  Daily English Vlog Report · {TODAY}")
    print(f"  Runtime: {'GitHub Actions' if IS_CI else 'Local'}")
    print(f"{'='*55}\n")

    # --- 1. 搜索视频 ---
    log("STEP 1: Searching YouTube...")
    queries = [
        "english vlog daily life",
        "day in my life english vlog",
        "english vlog daily routine",
        "english speaking vlog practice",
    ]
    videos = []
    seen = set()
    for q in queries:
        if len(videos) >= 5:
            break
        for v in search_videos(q, count=3):
            if v["id"] not in seen:
                seen.add(v["id"])
                videos.append(v)

    if not videos:
        log("ERROR: No videos found. YouTube may be blocking the GitHub Actions IP.")
        log("To fix: add YOUTUBE_COOKIES secret with Netscape-format cookies from a logged-in browser.")
        log("Export cookies with: yt-dlp --cookies-from-browser chrome --cookies cookies.txt")
        return 1

    log(f"Found {len(videos)} videos, targeting top 5")

    # --- 2. 下载字幕 + 分析 ---
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for i, v in enumerate(videos[:5]):
        log(f"\nSTEP 2 [{i+1}/5]: {v['title'][:60]}...")
        subtitle = download_subtitle(v["id"])
        if not subtitle or len(subtitle.split()) < 30:
            log(f"  Skipped: insufficient subtitle ({len(subtitle.split())} words)")
            continue

        log(f"  Subtitle: {len(subtitle)} chars, analyzing...")
        analysis = analyze_subtitle(subtitle, v)
        results.append((v, analysis))

    if not results:
        log("ERROR: No usable video results (no subtitles found for any video)")
        return 1

    # --- 3. 生成报告 ---
    log(f"\nSTEP 3: Generating report from {len(results)} videos...")
    report = generate_report(results)
    report_path = OUTPUT_DIR / f"report_{TODAY}.md"
    report_path.write_text(report, encoding="utf-8")
    log(f"Report saved: {report_path}")

    # --- 4. Notion ---
    log("\nSTEP 4: Syncing to Notion...")
    n = sync_notion(TODAY, report)

    # --- 5. 飞书 ---
    log("\nSTEP 5: Sending to Feishu...")
    f = send_feishu(report)

    # --- 清理 ---
    if SUBTITLE_DIR.exists():
        shutil.rmtree(SUBTITLE_DIR, ignore_errors=True)

    # --- 总结 ---
    print(f"\n{'='*55}")
    print(f"  DONE · {TODAY}")
    print(f"  Videos analyzed : {len(results)}")
    print(f"  Words to Notion : {n}")
    print(f"  Feishu sent     : {'OK' if f else 'FAIL'}")
    print(f"  Report          : {report_path}")
    print(f"{'='*55}\n")

    # 报告生成了就算成功（Notion/Feishu 失败不阻塞）
    return 0


if __name__ == "__main__":
    sys.exit(main())
