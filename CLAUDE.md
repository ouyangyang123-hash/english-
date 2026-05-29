# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Two parallel pipelines exist; **standalone_report.py is the active one**.

```
Primary (GitHub Actions):
  GitHub cron 22:00 UTC → yt-dlp search YouTube → download auto-subs (.srt)
  → DeepSeek API vocabulary extraction → Markdown report
  → Notion API (词汇银行) + Feishu API (card message) + git push report back

Legacy (Claude Code local):
  Claude Code cron → WebSearch WeChat articles → Claude analysis
  → sync_to_notion.py + send_to_feishu.py
```

The primary pipeline runs on GitHub servers (US), so YouTube is accessible. Every run produces one `vlog文稿/{date}/` directory containing per-source files and a consolidated report.

## Repo & Secrets

- **GitHub**: `ouyangyang123-hash/english-` (private)
- **Remote** (via proxy): `git remote add origin https://github.com/ouyangyang123-hash/english-.git`
- Git push from China requires proxy: `git config http.proxy "http://127.0.0.1:7890"`
- All API keys are GitHub Secrets, never hardcoded. Scripts read them via `os.environ.get()`.
- 6 secrets required: `DEEPSEEK_API_KEY`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_OPEN_ID`, `NOTION_API_KEY`, `NOTION_DATABASE_ID`

## Key Files

| File | Role |
|------|------|
| `standalone_report.py` | **Active pipeline**. yt-dlp → DeepSeek → report → Notion → Feishu. Runs in GitHub Actions or locally. |
| `.github/workflows/daily-report.yml` | Cron schedule (UTC 22:00 = Beijing 6:00 AM), `workflow_dispatch` for manual trigger. |
| `sync_to_notion.py` | CLI tool: `python sync_to_notion.py [YYYY-MM-DD]`. Parses report markdown tables, writes to Notion database. |
| `send_to_feishu.py` | CLI tool. Reads markdown from file/stdin, sends Feishu interactive card message. |
| `daily_vlog_fetch.py` | **Legacy**. Bilibili API with Wbi signing. Kept as reference but no longer used. |
| `助理.md` | Outdated system doc (still references Claude Code cron). Update if pipeline changes. |

## Output Structure

```
vlog文稿/{YYYY-MM-DD}/
├── vlog_01.md       # Individual source transcript/analysis
├── vlog_02.md
├── vlog_03.md
├── vlog_04.md
├── vlog_05.md
└── report_{date}.md # Consolidated daily learning report
```

## Notion Database Schema

Column names are in Chinese:

```
单词/短语 (title)  |  中文释义 (rich_text)  |  原文例句 (rich_text)
话题分类 (select)  |  词性 (select)         |  掌握状态 (select: 新学/学习中/已掌握)
来源日期 (date)    |  复习次数 (number)     |  简易度EF (number)  |  间隔天数 (number)
```

## Running Locally

```bash
# Full pipeline (requires yt-dlp in PATH, network proxy for YouTube)
set DEEPSEEK_API_KEY=sk-... & set FEISHU_APP_ID=... & ... & python standalone_report.py

# Sync an existing report to Notion
python sync_to_notion.py 2026-05-29

# Send an existing report to Feishu
python -c "from send_to_feishu import send_card; send_card('Title', open('vlog文稿/2026-05-29/report_2026-05-29.md', encoding='utf-8').read()[:4800])"
```

## Dependencies

- Python 3.9+: `requests`
- GitHub Actions additionally installs: `yt-dlp` (pip), `ffmpeg` (apt)
- Notion API: uses `2022-06-28` version, 0.35s delay between writes to avoid rate limits
- DeepSeek API: OpenAI-compatible endpoint at `https://api.deepseek.com/v1/chat/completions`, model `deepseek-chat`, ~¥0.001 per analysis

## Common Tasks

- **Check workflow status**: Visit `https://github.com/ouyangyang123-hash/english-/actions` or use GitHub API
- **Trigger manual run**: `curl -X POST https://api.github.com/repos/ouyangyang123-hash/english-/actions/workflows/285282701/dispatches -H "Authorization: Bearer TOKEN" -H "Accept: application/vnd.github+json" -d '{"ref":"master"}'`
- **Add a new source/channel**: Edit the `queries` list in `standalone_report.py` `main()` function
- **Change AI model**: Set `DEEPSEEK_MODEL` env var or edit the hardcoded `"deepseek-chat"` in `analyze_subtitle()`
