# -*- coding: utf-8 -*-
"""
Telegram-бот ВКР: выбор темы из Notion, пошаговые брифы, чеклист, помощь.
"""
import os
import logging
from dotenv import load_dotenv
load_dotenv()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from bot.database import (
    init_db,
    ensure_student,
    set_selected_brief,
    get_selected_brief,
    add_help_request,
)
from bot.notion_client import (
    fetch_briefs,
    fetch_brief_content,
    get_page_title,
    page_url,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

NOTION_BRIEFS_PAGE_ID = os.environ.get("NOTION_BRIEFS_PAGE_ID", "")
_ADMIN_IDS_RAW = os.environ.get("VKR_ADMIN_IDS", "354573537").strip()
ADMIN_IDS = set(int(x) for x in _ADMIN_IDS_RAW.split() if x.strip())


def get_briefs(context: ContextTypes.DEFAULT_TYPE):
    """Кэш брифов в bot_data (обновляется при старте и по необходимости)."""
    if "briefs" not in context.bot_data or not context.bot_data["briefs"]:
        context.bot_data["briefs"] = fetch_briefs(NOTION_BRIEFS_PAGE_ID)
    return context.bot_data["briefs"]


def get_brief_content(context: ContextTypes.DEFAULT_TYPE, page_id: str):
    """Контент страницы брифа (можно закэшировать по page_id в user_data или bot_data)."""
    cache = context.bot_data.setdefault("brief_content", {})
    if page_id not in cache:
        cache[page_id] = fetch_brief_content(page_id)
    return cache[page_id]


def _back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Назад» в меню раздела."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data="menu_back")]])


def _topic_only(title: str) -> str:
    """Убирает префикс 'Бриф для студента: ', оставляет только тему."""
    if not title:
        return title
    prefix = "Бриф для студента: "
    return title[len(prefix):].strip() if title.startswith(prefix) else title


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_student(user.id, user.username, user.first_name, user.last_name)

    briefs = get_briefs(context)
    if not briefs:
        await update.message.reply_text(
            "Список тем ВКР временно недоступен. Попробуйте позже или обратитесь к куратору."
        )
        return

    buttons = []
    for i, b in enumerate(briefs):
        if b.get("type") != "child_page":
            continue
        full_title = b.get("title") or ""
        if full_title.startswith("Задачи для ВКР"):
            continue
        title = _topic_only(full_title)[:50]
        buttons.append([InlineKeyboardButton(title, callback_data=f"brief:{i}")])

    if not buttons:
        await update.message.reply_text(
            "Список тем ВКР временно пуст. Обратитесь к куратору."
        )
        return
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "Выберите тему ВКР:",
        reply_markup=keyboard,
    )


async def callback_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    ensure_student(user.id, user.username, user.first_name, user.last_name)

    data = query.data
    if data.startswith("brief:"):
        idx = int(data.split(":")[1])
        briefs = get_briefs(context)
        if idx < 0 or idx >= len(briefs):
            await query.edit_message_text("Тема не найдена.")
            return
        brief = briefs[idx]
        if brief.get("type") != "child_page":
            await query.edit_message_text("Выберите тему из списка (страница брифов).")
            return
        set_selected_brief(user.id, idx)
        page_id = brief["page_id"]
        title = _topic_only(brief.get("title", "Бриф"))
        url = page_url(page_id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Открыть бриф в Notion", url=url)],
            [
                InlineKeyboardButton("✅ Чеклист", callback_data="menu:checklist"),
                InlineKeyboardButton("🖥 Окружение", callback_data="menu:environment"),
            ],
            [
                InlineKeyboardButton("📦 Продукт", callback_data="menu:product"),
                InlineKeyboardButton("📋 Шаги по порядку", callback_data="menu:steps"),
            ],
            [InlineKeyboardButton("🆘 Нужна помощь / встреча", callback_data="menu:help")],
        ])
        await query.edit_message_text(
            f"Тема: {title}\n\nВыберите раздел или откройте бриф в Notion:",
            reply_markup=keyboard,
        )
        return

    if data.startswith("menu:"):
        kind = data.split(":")[1]
        brief_index = get_selected_brief(user.id)
        if brief_index is None:
            await query.edit_message_text("Сначала выберите тему: /start")
            return
        briefs = get_briefs(context)
        if brief_index >= len(briefs):
            await query.edit_message_text("Тема не найдена. Выберите снова: /start")
            return
        brief = briefs[brief_index]
        page_id = brief["page_id"]
        url = page_url(page_id)
        content = get_brief_content(context, page_id)

        if kind == "checklist":
            items = content.get("checklist", [])
            if not items:
                text = "Чеклист в брифе не найден.\n\nОткройте бриф в Notion: " + url
            else:
                lines = ["Чеклист:\n"]
                for it in items:
                    mark = "✅" if it.get("checked") else "☐"
                    lines.append(f"{mark} {it.get('text', '')}")
                text = "\n".join(lines) + f"\n\nПодробнее в Notion: {url}"
            await query.edit_message_text(text, reply_markup=_back_keyboard())

        elif kind == "environment":
            sec = content.get("sections", {}).get("environment", {})
            title = sec.get("title", "Окружение / инфраструктура")
            preview = sec.get("preview", "")
            text = f"🖥 {title}\n\n{preview}\n\nОткрыть раздел в Notion: {url}"
            if not preview:
                text = f"🖥 {title}\n\nПодробности в брифе в Notion: {url}"
            await query.edit_message_text(text, reply_markup=_back_keyboard())

        elif kind == "product":
            sec = content.get("sections", {}).get("product", {})
            title = sec.get("title", "Выбор демо-приложения / продукта")
            preview = sec.get("preview", "")
            text = f"📦 {title}\n\n{preview}\n\nОткрыть раздел в Notion: {url}"
            if not preview:
                text = f"📦 {title}\n\nПодробности в брифе в Notion: {url}"
            await query.edit_message_text(text, reply_markup=_back_keyboard())

        elif kind == "steps":
            steps = content.get("steps", [])
            if not steps:
                text = f"Шаги не найдены.\n\nОткройте бриф в Notion: {url}"
                await query.edit_message_text(text, reply_markup=_back_keyboard())
                return
            context.user_data["brief_steps"] = steps
            context.user_data["brief_step_index"] = 0
            context.user_data["brief_page_url"] = url
            step = steps[0]
            msg = _format_step(step, 1, len(steps), url)
            keyboard = _steps_keyboard(0, len(steps))
            await query.edit_message_text(msg, reply_markup=keyboard)

        elif kind == "help":
            add_help_request(user.id, "meeting", "")
            # Уведомление админу
            who = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or "Без имени"
            admin_text = (
                f"🆘 Запрос на помощь/встречу\n\n"
                f"Кто: {who}\n"
                f"Username: @{user.username or '—'}\n"
                f"ID: {user.id}"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=admin_text)
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление админу %s: %s", admin_id, e)
            await query.edit_message_text(
                "Заявка на помощь/встречу отправлена. С вами свяжутся.",
                reply_markup=_back_keyboard(),
            )
        return

    if data.startswith("step:"):
        # навигация по шагам: step:prev / step:next / step:0
        brief_index = get_selected_brief(user.id)
        if brief_index is None:
            await query.answer("Сначала выберите тему: /start")
            return
        direction = data.split(":")[1]
        steps = context.user_data.get("brief_steps", [])
        idx = context.user_data.get("brief_step_index", 0)
        url = context.user_data.get("brief_page_url", "")
        if not steps:
            await query.answer("Шаги не загружены. Выберите 'Шаги по порядку' снова.")
            return
        n = len(steps)
        if direction == "prev":
            idx = max(0, idx - 1)
        elif direction == "next":
            idx = min(n - 1, idx + 1)
        else:
            try:
                idx = int(direction)
                idx = max(0, min(n - 1, idx))
            except ValueError:
                idx = 0
        context.user_data["brief_step_index"] = idx
        step = steps[idx]
        msg = _format_step(step, idx + 1, n, url)
        keyboard = _steps_keyboard(idx, n, url)
        await query.edit_message_text(msg, reply_markup=keyboard)
        await query.answer()
        return

    if data == "menu_back":
        brief_index = get_selected_brief(user.id)
        if brief_index is None:
            await query.edit_message_text("Сначала выберите тему: /start")
            return
        briefs = get_briefs(context)
        if brief_index >= len(briefs):
            await query.edit_message_text("Тема не найдена. /start")
            return
        brief = briefs[brief_index]
        title = _topic_only(brief.get("title", "Бриф"))
        url = page_url(brief["page_id"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Открыть бриф в Notion", url=url)],
            [
                InlineKeyboardButton("✅ Чеклист", callback_data="menu:checklist"),
                InlineKeyboardButton("🖥 Окружение", callback_data="menu:environment"),
            ],
            [
                InlineKeyboardButton("📦 Продукт", callback_data="menu:product"),
                InlineKeyboardButton("📋 Шаги по порядку", callback_data="menu:steps"),
            ],
            [InlineKeyboardButton("🆘 Нужна помощь / встреча", callback_data="menu:help")],
        ])
        await query.edit_message_text(
            f"Тема: {title}\n\nВыберите раздел или откройте бриф в Notion:",
            reply_markup=keyboard,
        )


def _format_step(step: dict, num: int, total: int, url: str) -> str:
    title = step.get("title", "")
    preview = step.get("content_preview", "")
    return f"Шаг {num}/{total}: {title}\n\n{preview}\n\nПодробнее в Notion: {url}"


def _steps_keyboard(current: int, total: int, url: str) -> InlineKeyboardMarkup:
    row = []
    if current > 0:
        row.append(InlineKeyboardButton("◀ Пред", callback_data="step:prev"))
    if current < total - 1:
        row.append(InlineKeyboardButton("След ▶", callback_data="step:next"))
    row.append(InlineKeyboardButton("В меню", callback_data="menu_back"))
    return InlineKeyboardMarkup([row])


def main():
    init_db()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_brief))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
