#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вывести структуру одной страницы-брифа (типы блоков и текст)."""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bot.notion_client import get_blocks, get_page_title, _plain_text, fetch_briefs

def main():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("NOTION_TOKEN required")
        sys.exit(1)
    briefs = fetch_briefs(os.environ.get("NOTION_BRIEFS_PAGE_ID", "2ab9188dc2f28045badcc8786fda551d"), token)
    # берём первый бриф-страницу (индекс 1 — "Бриф для студента: ...")
    if len(briefs) < 2:
        print("Need at least 2 briefs")
        sys.exit(1)
    page_id = briefs[1]["page_id"]
    title = get_page_title(page_id, token)
    print(f"Page: {title}\n")
    blocks = get_blocks(page_id, token)
    for i, b in enumerate(blocks):
        t = b.get("type")
        text = _plain_text(b)
        if t == "to_do":
            checked = (b.get("to_do") or {}).get("checked")
            text = f"[{'x' if checked else ' '}] " + text
        print(f"{i:2}. {t:20} {repr(text)[:80]}")
    # optionally dump one to_do block structure
    for b in blocks:
        if b.get("type") == "to_do":
            print("\nSample to_do block:", json.dumps(b, ensure_ascii=False, indent=2)[:500])
            break

if __name__ == "__main__":
    main()
