#!/usr/bin/env python3
"""
微信公众号英语Vlog文稿抓取脚本（本地管线）
从微信公众号文章搜索并抓取英语Vlog学习内容，保存到 vlog文稿/{日期}/

内容来源:
  - 微信公众号英语教学账号（卿公子鲤、Vlog口语精华、旅行英语急救包 等）
  - 搜狗微信搜索 / Bing 搜索发现的英语口语文章
  - 手动指定的公众号文章 URL

使用方法:
  python daily_vlog_fetch.py                              # 搜索并抓取今日文章
  python daily_vlog_fetch.py --url <微信文章URL>           # 抓取指定文章
  python daily_vlog_fetch.py --paste <文件.txt>            # 从粘贴的文本导入（推荐）
  python daily_vlog_fetch.py --paste-dir <目录>            # 批量导入目录下所有.txt
  python daily_vlog_fetch.py --urls urls.txt               # 批量抓取URL列表
  python daily_vlog_fetch.py --search "英语vlog口语"       # 按关键词搜索

粘贴文件格式:
  第1行: 文章标题
  第2行: 公众号名称
  第3行: 原文URL (可选)
  第4行起: 正文内容

注意:
  微信公众号 (mp.weixin.qq.com) 通常会阻止自动化抓取。
  推荐用法：在微信中复制文章内容 → 粘贴到 .txt 文件 → 用 --paste 导入。

与 YouTube 管线的关系:
  本脚本 = 微信公众号内容源（国内本地运行）
  standalone_report.py = YouTube 视频源（GitHub Actions 美国运行）
  两条管线互为备份，输出格式兼容，可共用后续的分析/推送工具。
"""

import os
import re
import sys
import json
import time
import hashlib
import datetime
import urllib.parse
import html as html_mod
from pathlib import Path
from html.parser import HTMLParser

import requests

# ============================================================
# 配置
# ============================================================

TODAY = datetime.date.today().strftime("%Y-%m-%d")
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "vlog文稿" / TODAY

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

# ============================================================
# 微信文章抓取
# ============================================================

class WeChatArticleParser(HTMLParser):
    """解析微信公众号文章 HTML，提取正文内容和元信息"""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.author = ""
        self.content_lines = []
        self._in_title = False
        self._in_content = False
        self._in_author = False
        self._skip = False
        self._tag_stack = []
        self._current_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "h1" and attrs_dict.get("id") == "activity-name":
            self._in_title = True
        elif tag == "div" and attrs_dict.get("id") == "js_content":
            self._in_content = True
        elif tag == "span" and attrs_dict.get("id") == "js_name":
            self._in_author = True
        elif self._in_content and tag in ("p", "div", "section", "h1", "h2", "h3", "h4"):
            self._tag_stack.append(tag)
            if self._current_text.strip():
                self.content_lines.append(self._current_text.strip())
                self._current_text = ""
        elif self._in_content and tag == "br":
            if self._current_text.strip():
                self.content_lines.append(self._current_text.strip())
                self._current_text = ""
        elif tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag == "h1" and self._in_title:
            self._in_title = False
        elif tag == "span" and self._in_author:
            self._in_author = False
        elif self._in_content and tag in ("p", "div", "section", "h1", "h2", "h3", "h4"):
            if self._tag_stack and self._tag_stack[-1] == tag:
                self._tag_stack.pop()
            if self._current_text.strip():
                self.content_lines.append(self._current_text.strip())
                self._current_text = ""
        elif tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._in_author:
            self.author += data
        elif self._in_content:
            text = data.strip()
            if text:
                self._current_text += text


def fetch_wechat_article(url):
    """抓取单篇微信公众号文章，返回标题、作者、正文"""
    # 清理 URL：移除微信跳转壳
    url = url.strip()
    if "weixin.qq.com" not in url and "mp.weixin.qq.com" not in url:
        # 可能是搜狗搜索的跳转链接
        pass

    headers = {
        "Referer": "https://mp.weixin.qq.com/",
    }

    try:
        resp = SESSION.get(url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code} fetching article")
            return None

        html = resp.text

        # 尝试从 meta 标签提取标题
        title = ""
        title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
        if not title_match:
            title_match = re.search(r'<meta\s+name="twitter:title"\s+content="([^"]*)"', html)
        if title_match:
            title = html_mod.unescape(title_match.group(1).strip())

        # 从 meta 提取作者
        author = ""
        author_match = re.search(r'<span id="js_name">([^<]*)</span>', html)
        if author_match:
            author = author_match.group(1).strip()
        if not author:
            author_match = re.search(r'<meta\s+name="author"\s+content="([^"]*)"', html)
            if author_match:
                author = author_match.group(1).strip()

        # 提取发布日期
        date_match = re.search(r'<span id="publish_time"[^>]*>([^<]*)</span>', html)
        pub_date = date_match.group(1).strip() if date_match else TODAY

        # 提取正文内容（js_content div）
        content_pat = re.compile(
            r'<div\s+id="js_content"\s+class="[^"]*rich_media_content[^"]*"[^>]*>(.*?)</div>\s*<script',
            re.DOTALL,
        )
        content_match = content_pat.search(html)
        if not content_match:
            # 尝试更宽松的匹配
            content_match = re.search(
                r'id="js_content"[^>]*>(.*?)</div>\s*(?:<script|</div>)',
                html, re.DOTALL,
            )

        if not content_match:
            print("    Could not extract article content from HTML")
            return None

        raw_content = content_match.group(1)

        # 清洗 HTML → 纯文本/Markdown
        clean = clean_wechat_html(raw_content)

        if not title:
            parser = WeChatArticleParser()
            parser.feed(html)
            title = parser.title.strip()

        if not title:
            title = f"微信文章_{TODAY}"

        print(f"    标题: {title[:60]}")
        print(f"    作者: {author or '未知'}")

        return {
            "title": title,
            "author": author or "未知公众号",
            "url": url,
            "date": pub_date,
            "content": clean,
        }

    except requests.Timeout:
        print(f"    Timeout fetching: {url}")
        return None
    except Exception as e:
        print(f"    Error fetching article: {e}")
        return None


def clean_wechat_html(html_content):
    """清洗微信文章 HTML，转为可读的 Markdown 文本"""
    text = html_content

    # 解码 HTML 实体
    text = html_mod.unescape(text)

    # 移除 style/img/script 标签及其内容
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<img[^>]*/?>', '', text)
    text = re.sub(r'<svg[^>]*>.*?</svg>', '', text, flags=re.DOTALL)

    # 移除 HTML 属性但保留标签结构
    text = re.sub(r'<(\w+)(?:\s[^>]*)?>', r'<\1>', text)

    # 把块级元素转为换行
    for tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'br'):
        text = re.sub(rf'</?{tag}[^>]*>', '\n', text, flags=re.IGNORECASE)

    # 移除所有剩余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)

    # 处理微信特有格式
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)

    # 压缩多余空行（最多保留1个空行）
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)

    # 去重连续重复的行
    lines = text.split('\n')
    result = []
    prev = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev:
            result.append(line)
            prev = stripped
        elif not stripped and result and result[-1] != '':
            result.append('')

    return '\n'.join(result).strip()


# ============================================================
# 文章搜索
# ============================================================

def search_wechat_articles(keyword, count=10):
    """通过搜狗微信搜索或Bing查找微信公众号文章"""
    articles = []

    # 方法1: 搜狗微信搜索
    sogou_results = _search_sogou_wechat(keyword, count)
    articles.extend(sogou_results)

    # 方法2: Bing 搜索（搜狗不足时补充）
    if len(articles) < 3:
        time.sleep(1)
        bing_results = _search_bing_wechat(keyword, count)
        for br in bing_results:
            if br["url"] not in {a["url"] for a in articles}:
                articles.append(br)

    return articles[:count]


def _search_sogou_wechat(keyword, count=10):
    """搜狗微信搜索"""
    results = []
    try:
        encoded = urllib.parse.quote(keyword)
        url = f"https://weixin.sogou.com/weixin?type=2&query={encoded}&ie=utf8"

        headers = {
            "Referer": "https://weixin.sogou.com/",
        }
        resp = SESSION.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return results

        html = resp.text

        # 搜狗搜索结果中的文章链接格式
        # <a href="https://mp.weixin.qq.com/s?..." ...>标题</a>
        link_pattern = re.compile(
            r'<a\s+[^>]*href="(https?://mp\.weixin\.qq\.com/s[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        for match in link_pattern.finditer(html):
            article_url = match.group(1)
            # 搜狗链接可能是加密跳转，需要转换
            if "weixin.sogou.com" in article_url:
                article_url = html_mod.unescape(article_url)

            title_raw = match.group(2)
            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            title = html_mod.unescape(title)

            # 过滤非英语学习相关内容
            if not _is_english_learning(title):
                continue

            if len(results) >= count:
                break

            results.append({
                "title": title,
                "url": article_url,
                "source": "sogou",
            })

    except Exception as e:
        print(f"    Sogou search error: {e}")

    return results


def _search_bing_wechat(keyword, count=10):
    """Bing 搜索微信公众号文章"""
    results = []
    try:
        encoded = urllib.parse.quote(f"{keyword} site:mp.weixin.qq.com")
        url = f"https://www.bing.com/search?q={encoded}&count={count}"

        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            return results

        html = resp.text

        # Bing 搜索结果
        cite_pattern = re.compile(
            r'<cite[^>]*>(https?://mp\.weixin\.qq\.com/s[^<]*)</cite>'
        )
        link_pattern = re.compile(
            r'<a\s+href="(https?://mp\.weixin\.qq\.com/s[^"]*)"[^>]*>([^<]+)</a>'
        )

        found_cites = cite_pattern.findall(html)
        found_links = link_pattern.findall(html)

        for link_url, title in found_links:
            title = html_mod.unescape(title.strip())
            if not title or "http" in title:
                continue
            if not _is_english_learning(title):
                continue
            if len(results) >= count:
                break
            results.append({
                "title": title,
                "url": link_url,
                "source": "bing",
            })

        for cite_url in found_cites:
            if len(results) >= count:
                break
            cite_url = cite_url.strip()
            if cite_url not in {r["url"] for r in results}:
                results.append({
                    "title": "(Bing发现)",
                    "url": cite_url,
                    "source": "bing",
                })

    except Exception as e:
        print(f"    Bing search error: {e}")

    return results


def _is_english_learning(title):
    """判断文章标题是否与英语学习相关"""
    title_lower = title.lower()
    keywords = [
        "英语", "english", "口语", "vlog", "单词", "词汇", "短语",
        "表达", "句型", "俚语", "地道", "学习", "跟读", "听力",
        "speaking", "learn", "vocab", "phrase", "idiom", "slang",
        "ielts", "toefl", "bbc", "日常", "旅行", "职场",
    ]
    return any(kw in title_lower for kw in keywords)


# ============================================================
# 本地存储
# ============================================================

def save_article(article, index):
    """保存单篇文章到 vlog文稿/{日期}/vlog_XX.md"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    file_path = OUTPUT_DIR / f"vlog_{index:02d}.md"

    content = f"# {article['title']}\n"
    content += f"**来源**: 微信公众号「{article['author']}」\n"
    content += f"**日期**: {article.get('date', TODAY)}\n"
    content += f"**链接**: {article['url']}\n"
    content += f"\n---\n\n"
    content += article['content']

    file_path.write_text(content, encoding="utf-8")
    print(f"    Saved: {file_path}")
    return file_path


# ============================================================
# 粘贴模式：从本地文本文件读取用户复制的微信文章
# ============================================================

def import_from_paste(file_path):
    """从粘贴的文本文件导入微信文章
    文件格式：
      第1行: 标题
      第2行: 公众号名称
      第3行: 原文URL (可选)
      第4行起: 正文内容
    """
    path = Path(file_path)
    if not path.exists():
        print(f"    File not found: {file_path}")
        return None

    text = path.read_text(encoding="utf-8")
    lines = text.strip().split("\n")

    title = lines[0].strip() if lines else "未命名"
    author = lines[1].strip() if len(lines) > 1 else "未知公众号"
    url = ""
    body_start = 2

    if len(lines) > 2 and ("mp.weixin.qq.com" in lines[2] or "http" in lines[2]):
        url = lines[2].strip()
        body_start = 3

    content = "\n".join(lines[body_start:])

    return {
        "title": title,
        "author": author,
        "url": url,
        "content": content.strip(),
    }


# ============================================================
# 主流程
# ============================================================

def main():
    print(f"\n{'='*55}")
    print(f"  微信公众号英语Vlog文稿抓取 · {TODAY}")
    print(f"{'='*55}\n")

    articles_to_fetch = []

    # 模式1: --paste <文件>  从粘贴的文本文件导入
    # 模式2: --paste-dir <目录>  批量导入目录下所有.txt文件
    # 模式3: --url <微信公众号URL>  尝试直接抓取
    # 模式4: --search <关键词>  搜索微信公众号文章

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--paste" and i + 1 < len(args):
            i += 1
            file_path = args[i]
            result = import_from_paste(file_path)
            if result:
                articles_to_fetch.append({
                    "title": result["title"],
                    "url": result["url"] or "(本地粘贴)",
                    "source": "paste",
                    "_content": result["content"],
                    "_author": result["author"],
                })

        elif arg == "--paste-dir" and i + 1 < len(args):
            i += 1
            dir_path = Path(args[i])
            if dir_path.is_dir():
                for txt_file in sorted(dir_path.glob("*.txt")):
                    result = import_from_paste(str(txt_file))
                    if result:
                        articles_to_fetch.append({
                            "title": result["title"],
                            "url": result["url"] or "(本地粘贴)",
                            "source": "paste",
                            "_content": result["content"],
                            "_author": result["author"],
                        })

        elif arg == "--url" and i + 1 < len(args):
            i += 1
            url = args[i]
            if "mp.weixin.qq.com" in url or "weixin" in url:
                articles_to_fetch.append({
                    "title": "(指定文章)", "url": url, "source": "manual",
                })

        elif arg == "--urls" and i + 1 < len(args):
            i += 1
            urls_file = Path(args[i])
            if urls_file.exists():
                for line in urls_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        articles_to_fetch.append({
                            "title": "(批量导入)", "url": line, "source": "batch",
                        })

        elif arg == "--search" and i + 1 < len(args):
            i += 1
            keyword = args[i]
            print(f"  Searching: '{keyword}'")
            results = search_wechat_articles(keyword, count=8)
            articles_to_fetch.extend(results)

        i += 1

    # 默认：搜索模式
    if not articles_to_fetch:
        keyword = "英语Vlog口语地道表达"
        print(f"  Searching WeChat articles: '{keyword}'")
        print(f"  NOTE: 微信公众号可能阻止自动抓取。如果搜索无结果，请使用:")
        print(f"    --paste <文件>   从本地文本文件导入（推荐）")
        print(f"    --paste-dir <目录>  批量导入")
        print(f"    --url <链接>      尝试直接抓取\n")
        articles_to_fetch = search_wechat_articles(keyword, count=8)

    if not articles_to_fetch:
        print("\n  No articles found or fetched.")
        print("  推荐使用方法:")
        print("    1. 在微信中复制文章内容")
        print("    2. 粘贴到 .txt 文件（第1行标题，第2行公众号名，第3行起正文）")
        print("    3. python daily_vlog_fetch.py --paste article.txt")
        return 1

    print(f"\n  {len(articles_to_fetch)} article(s) to process\n")

    # 处理每篇文章
    success = 0
    for i, art in enumerate(articles_to_fetch[:5]):
        idx = i + 1
        title_preview = art["title"][:50]
        print(f"  [{idx}/{min(5, len(articles_to_fetch))}] {title_preview}...")

        # 粘贴模式：直接使用已有内容
        if art.get("source") == "paste" and "_content" in art:
            result = {
                "title": art["title"],
                "author": art.get("_author", "未知公众号"),
                "url": art["url"],
                "date": TODAY,
                "content": art["_content"],
            }
            if len(result["content"]) > 50:
                save_article(result, success + 1)
                success += 1
            else:
                print(f"    Skipped: content too short ({len(result['content'])} chars)")
        else:
            # 在线模式：直接抓取（可能被微信阻止）
            result = fetch_wechat_article(art["url"])
            if result and len(result.get("content", "")) > 100:
                save_article(result, success + 1)
                success += 1
            else:
                print(f"    Skipped: could not fetch (微信可能阻止了自动抓取)")
                print(f"    提示: 手动复制文章内容 → 保存为.txt → 用 --paste 导入")

        if i < min(5, len(articles_to_fetch)) - 1:
            time.sleep(1.5)

    print(f"\n{'='*55}")
    print(f"  DONE · {TODAY}")
    print(f"  Articles saved: {success} → {OUTPUT_DIR}")
    print(f"{'='*55}\n")
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
