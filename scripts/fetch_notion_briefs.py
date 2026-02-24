#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для просмотра содержимого страницы с брифами в Notion.
Запуск (токен из окружения или Keychain):
  export NOTION_TOKEN="$(security find-generic-password -a "$USER" -s notion-token -w 2>/dev/null)"
  export NOTION_BRIEFS_PAGE_ID="2ab9188dc2f28045badcc8786fda551d"
  python scripts/fetch_notion_briefs.py
"""
import os
import sys

# корень проекта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.notion_client import (
    get_blocks,
    get_page_title,
    fetch_briefs,
    page_url,
)

PAGE_ID = os.environ.get("NOTION_BRIEFS_PAGE_ID", "2ab9188dc2f28045badcc8786fda551d")


def main():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("Задайте NOTION_TOKEN (или из Keychain: security find-generic-password -a $USER -s notion-token -w)")
        sys.exit(1)

    title = get_page_title(PAGE_ID, token)
    print(f"Страница: {title or '(без названия)'}")
    print(f"URL: {page_url(PAGE_ID)}\n")

    briefs = fetch_briefs(PAGE_ID, token)
    if not briefs:
        # вывести сырые блоки для отладки
        blocks = get_blocks(PAGE_ID, token)
        print("Брифов (heading_1/2/3) не найдено. Сырые блоки (первые 30):")
        for i, b in enumerate(blocks[:30]):
            t = b.get("type", "?")
            rt = (b.get(t) or {}).get("rich_text") or []
            text = "".join(x.get("plain_text", "") for x in rt).strip()[:60]
            print(f"  {i+1}. [{t}] {text!r}")
        return

    print("Брифы:")
    for i, b in enumerate(briefs):
        indent = "  " * (b.get("level", 1) - 1)
        desc = b.get("description", "")
        line = f"{i}. {indent}{b['title']}"
        if desc:
            line += f" — {desc[:50]}..."
        pid = b.get("page_id")
        if pid:
            line += f"\n   URL: {page_url(pid)}"
        print(line)


if __name__ == "__main__":
    main()
