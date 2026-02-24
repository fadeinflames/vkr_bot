# -*- coding: utf-8 -*-
"""
Клиент Notion API для страницы с брифами.
Переменные (как в infra): NOTION_TOKEN, NOTION_BRIEFS_PAGE_ID.
"""
import os
import re
import requests

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com/v1"


def _norm_id(page_id: str) -> str:
    """Приводит ID страницы к формату с дефисами (UUID), если передан без них."""
    s = (page_id or "").replace("-", "").strip()
    if len(s) != 32 or not re.match(r"^[0-9a-fA-F]+$", s):
        return page_id or ""
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def _plain_text(block: dict) -> str:
    """Достаёт plain text из блока (rich_text)."""
    block_type = block.get("type")
    if not block_type or block_type not in block:
        return ""
    rich = block.get(block_type, {}).get("rich_text") or []
    return "".join(item.get("plain_text", "") for item in rich).strip()


def get_blocks(page_id: str, token: str = None) -> list:
    """
    Возвращает все блоки первого уровня страницы (с пагинацией).
    page_id: ID страницы (из URL, можно с дефисами или без).
    token: NOTION_TOKEN (если не передан — из env).
    """
    token = token or os.environ.get("NOTION_TOKEN")
    if not token:
        return []
    pid = _norm_id(page_id)
    if not pid:
        return []
    url = f"{BASE}/blocks/{pid}/children"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    results = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        results.extend(data.get("results") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return results


def get_page_title(page_id: str, token: str = None) -> str:
    """Возвращает заголовок страницы (properties.title)."""
    token = token or os.environ.get("NOTION_TOKEN")
    if not token:
        return ""
    pid = _norm_id(page_id)
    if not pid:
        return ""
    url = f"{BASE}/pages/{pid}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
    }
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        return ""
    data = r.json()
    props = data.get("properties") or {}
    # Заголовок страницы обычно в title
    for key, val in props.items():
        if isinstance(val, dict) and val.get("type") == "title":
            title_arr = val.get("title") or []
            return "".join(t.get("plain_text", "") for t in title_arr).strip()
    return ""


def _title_from_child_page(block: dict) -> str:
    """Заголовок из блока child_page (иногда есть в самом блоке)."""
    cp = block.get("child_page") or {}
    return (cp.get("title") or "").strip()


def parse_briefs(blocks: list, token: str = None) -> list:
    """
    Превращает блоки в список «брифов»:
    - child_page → бриф с page_id и title (заголовок страницы, при необходимости запрос к API);
    - heading_1/2/3 → бриф с title и level.
    """
    token = token or os.environ.get("NOTION_TOKEN")
    briefs = []
    for b in blocks:
        t = b.get("type")
        bid = b.get("id")
        if t == "child_page":
            title = _title_from_child_page(b)
            if not title and token:
                title = get_page_title(bid, token)
            briefs.append({
                "title": title or "(без названия)",
                "type": t,
                "block_id": bid,
                "page_id": bid,
                "level": 1,
            })
            continue
        text = _plain_text(b)
        if not text and t not in ("heading_1", "heading_2", "heading_3"):
            continue
        level = {"heading_1": 1, "heading_2": 2, "heading_3": 3}.get(t)
        if level is not None:
            briefs.append({"title": text, "type": t, "block_id": bid, "level": level})
        elif t == "paragraph" and briefs and "description" not in briefs[-1]:
            briefs[-1]["description"] = text
    return briefs


def fetch_briefs(page_id: str = None, token: str = None) -> list:
    """
    Загружает страницу и возвращает список брифов.
    Поддерживаются дочерние страницы (child_page) и заголовки (heading_1/2/3).
    page_id по умолчанию из NOTION_BRIEFS_PAGE_ID.
    """
    page_id = page_id or os.environ.get("NOTION_BRIEFS_PAGE_ID", "")
    blocks = get_blocks(page_id, token)
    return parse_briefs(blocks, token)


def page_url(page_id: str) -> str:
    """Ссылка на страницу в Notion (для браузера)."""
    pid = (page_id or "").replace("-", "")
    if not pid:
        return ""
    return f"https://www.notion.so/{pid}"


# --- Парсинг контента одной страницы-брифа (шаги, чеклист, секции) ---


def _to_do_text(block: dict) -> tuple:
    """Текст to_do и флаг checked. Возвращает (text, checked)."""
    payload = block.get("to_do") or {}
    text = _plain_text(block)
    return text, payload.get("checked", False)


def parse_brief_page(blocks: list) -> dict:
    """
    Разбирает блоки страницы брифа.
    Возвращает:
      steps: список шагов по heading_2 [{index, title, content_preview}],
      checklist: список to_do [{text, checked}],
      sections: словарь по ключам "environment" / "product" — заголовок и превью (по ключевым словам в heading_2).
    """
    steps = []
    checklist = []
    sections = {}
    current_content = []

    # Лимит превью для шага (секции «Продукт»/«Окружение» — полный список, лимит Telegram 4096)
    PREVIEW_MAX = 3600

    def flush_content():
        nonlocal current_content
        if current_content and steps:
            steps[-1]["content_preview"] = "\n".join(current_content)[:PREVIEW_MAX]
        current_content = []

    for b in blocks:
        t = b.get("type")
        text = _plain_text(b)

        if t == "heading_2":
            flush_content()
            current_content = []
            steps.append({"index": len(steps) + 1, "title": text, "content_preview": ""})
            # маппинг на кнопки «Окружение» / «Продукт»
            lower = text.lower()
            if "инфраструктур" in lower or "окружен" in lower or "кластер" in lower:
                sections["environment"] = {"title": text, "preview": ""}
            elif "демо-приложен" in lower or "выбор приложен" in lower or "продукт" in lower:
                sections["product"] = {"title": text, "preview": ""}
        elif t == "heading_3" and steps:
            current_content.append(text)
        elif t == "paragraph" and text and steps:
            current_content.append(text[:400])
        elif t == "to_do":
            item_text, checked = _to_do_text(b)
            checklist.append({"text": item_text, "checked": checked})
        elif t == "bulleted_list_item" and text and steps:
            current_content.append("• " + text[:280])
        elif t == "numbered_list_item" and text and steps:
            current_content.append(text[:280])

    flush_content()

    # превью для секций environment/product — полный текст раздела (до лимита Telegram ~4k)
    for step in steps:
        lower = step["title"].lower()
        prev = (step.get("content_preview") or "")[:3600]
        if "инфраструктур" in lower or "окружен" in lower or "кластер" in lower:
            sections["environment"] = {"title": step["title"], "preview": prev}
        elif "демо-приложен" in lower or "выбор приложен" in lower or "приложен" in lower:
            sections["product"] = {"title": step["title"], "preview": prev}

    return {"steps": steps, "checklist": checklist, "sections": sections}


def fetch_brief_content(brief_page_id: str, token: str = None) -> dict:
    """Загружает контент страницы брифа и возвращает структуру parse_brief_page."""
    token = token or os.environ.get("NOTION_TOKEN")
    if not token or not brief_page_id:
        return {"steps": [], "checklist": [], "sections": {}}
    blocks = get_blocks(brief_page_id, token)
    return parse_brief_page(blocks)
