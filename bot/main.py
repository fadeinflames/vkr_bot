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
    MessageHandler,
    ContextTypes,
    filters,
)

from bot.database import (
    init_db,
    ensure_student,
    set_selected_brief,
    get_selected_brief,
    add_help_request,
    set_checklist_item,
    get_checklist_checked,
    get_all_checklist_results,
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


def _checklist_message(items: list, checked: set, url: str, brief_index: int) -> tuple:
    """Текст чеклиста и клавиатура с кнопками переключения по пунктам."""
    lines = ["Чеклист (нажми пункт, чтобы отметить):\n"]
    buttons = []
    for i, it in enumerate(items):
        done = i in checked
        mark = "✅" if done else "☐"
        line_text = (it.get("text") or "")[:60]
        lines.append(f"{mark} {i + 1}. {line_text}")
        btn_label = f"{'✅' if done else '☐'} {i + 1}"
        buttons.append([InlineKeyboardButton(btn_label, callback_data=f"chk:{brief_index}:{i}")])
    text = "\n".join(lines) + f"\n\nПодробнее в Notion: {url}"
    buttons.append([InlineKeyboardButton("◀ Назад", callback_data="menu_back")])
    return text, InlineKeyboardMarkup(buttons)


def _topic_only(title: str) -> str:
    """Убирает префикс 'Бриф для студента: ', оставляет только тему."""
    if not title:
        return title
    prefix = "Бриф для студента: "
    return title[len(prefix):].strip() if title.startswith(prefix) else title


async def progress_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа: прогресс по чеклистам студентов."""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("Недоступно.")
        return
    rows = get_all_checklist_results()
    if not rows:
        await update.message.reply_text("Пока ни у кого не выбран бриф с чеклистом.")
        return
    briefs = get_briefs(context)
    lines = ["Прогресс по чеклистам:\n"]
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"] or "—"
        bidx = r["brief_index"]
        done = r["completed_count"]
        total = 0
        if bidx is not None and bidx < len(briefs):
            content = get_brief_content(context, briefs[bidx]["page_id"])
            total = len(content.get("checklist", []))
        total = total or "?"
        lines.append(f"• {name} (@{r['username'] or '—'}): {done}/{total}")
    await update.message.reply_text("\n".join(lines))


async def _notify_admin_help(context: ContextTypes.DEFAULT_TYPE, kind: str, who: str, username: str, user_id: int, comment: str):
    kind_label = "Нужна помощь" if kind == "help" else "Нужен прогон/встреча"
    emoji = "🆘" if kind == "help" else "📅"
    admin_text = (
        f"{emoji} {kind_label}\n\n"
        f"Кто: {who}\n"
        f"Username: @{username or '—'}\n"
        f"ID: {user_id}\n\n"
        f"Текст: {comment}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_text)
        except Exception as e:
            logger.warning("Не удалось отправить уведомление админу %s: %s", admin_id, e)


async def handle_input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста: форма помощи или окна для встречи."""
    user = update.effective_user
    awaiting = context.user_data.pop("awaiting_input", None)
    if not awaiting:
        return
    text = (update.message.text or "").strip()
    if not text:
        context.user_data["awaiting_input"] = awaiting
        await update.message.reply_text("Напишите текст или нажмите Отмена в сообщении выше.")
        return
    ensure_student(user.id, user.username, user.first_name, user.last_name)
    add_help_request(user.id, awaiting, text)
    who = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or "Без имени"
    await _notify_admin_help(context, awaiting, who, user.username, user.id, text)
    await update.message.reply_text("Заявка отправлена. С вами свяжутся.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.pop("awaiting_input", None)
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
            [
                InlineKeyboardButton("🆘 Нужна помощь", callback_data="menu:help"),
                InlineKeyboardButton("📅 Нужен прогон/встреча", callback_data="menu:meeting"),
            ],
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
                await query.edit_message_text(text, reply_markup=_back_keyboard())
            else:
                checked = get_checklist_checked(user.id, brief_index)
                text, keyboard = _checklist_message(items, checked, url, brief_index)
                await query.edit_message_text(text, reply_markup=keyboard)

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
            context.user_data["awaiting_input"] = "help"
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="input_cancel")]])
            await query.edit_message_text(
                "Опишите, с чем нужна помощь (напишите текстом в чат):",
                reply_markup=cancel_kb,
            )
        elif kind == "meeting":
            context.user_data["awaiting_input"] = "meeting"
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="input_cancel")]])
            await query.edit_message_text(
                "Укажите удобные окна для встречи/прогона (например: пн 15:00, ср после 18:00). Напишите в чат:",
                reply_markup=cancel_kb,
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

    if data.startswith("chk:"):
        # Переключить пункт чеклиста: chk:brief_index:item_index
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer()
            return
        try:
            brief_idx = int(parts[1])
            item_idx = int(parts[2])
        except ValueError:
            await query.answer()
            return
        checked = get_checklist_checked(user.id, brief_idx)
        new_state = item_idx not in checked
        set_checklist_item(user.id, brief_idx, item_idx, new_state)
        # Обновить сообщение чеклиста
        briefs = get_briefs(context)
        if brief_idx >= len(briefs):
            await query.answer("Тема не найдена.")
            return
        page_id = briefs[brief_idx]["page_id"]
        content = get_brief_content(context, page_id)
        items = content.get("checklist", [])
        if item_idx >= len(items):
            await query.answer()
            return
        checked = get_checklist_checked(user.id, brief_idx)
        url = page_url(page_id)
        text, keyboard = _checklist_message(items, checked, url, brief_idx)
        await query.edit_message_text(text, reply_markup=keyboard)
        await query.answer("Отмечено" if new_state else "Снято")

    if data == "input_cancel":
        context.user_data.pop("awaiting_input", None)
        await query.edit_message_text("Ввод отменён.", reply_markup=_back_keyboard())

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
            [
                InlineKeyboardButton("🆘 Нужна помощь", callback_data="menu:help"),
                InlineKeyboardButton("📅 Нужен прогон/встреча", callback_data="menu:meeting"),
            ],
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
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input_message))
    app.add_handler(CallbackQueryHandler(callback_brief))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
