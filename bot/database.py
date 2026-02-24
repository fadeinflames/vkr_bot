# -*- coding: utf-8 -*-
"""SQLite-база: студенты, прогресс по брифам, запросы на встречи."""
import sqlite3
import os

DB_PATH = os.environ.get("VKR_DB_PATH", "vkr_bot.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    # Студенты (Telegram user_id как ключ),
    # selected_brief_index — выбранная тема ВКР,
    # current_step_index — следующий шаг в разделе "Шаги по порядку".
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            selected_brief_index INTEGER,
            current_step_index INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Прогресс: какой бриф отмечен выполненным
    cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER,
            brief_index INTEGER,
            completed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, brief_index),
            FOREIGN KEY (user_id) REFERENCES students(user_id)
        )
    """)
    # Запросы на встречу/помощь
    cur.execute("""
        CREATE TABLE IF NOT EXISTS help_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT,
            comment TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            resolved INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES students(user_id)
        )
    """)
    # Прогресс по чеклисту: студент отметил пункт (brief_index + item_index в рамках брифа)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS checklist_progress (
            user_id INTEGER,
            brief_index INTEGER,
            item_index INTEGER,
            completed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, brief_index, item_index),
            FOREIGN KEY (user_id) REFERENCES students(user_id)
        )
    """)
    # Колонки могут быть добавлены позже — пытаемся добавить их, игнорируя ошибки, если уже существуют.
    try:
        cur.execute("ALTER TABLE students ADD COLUMN selected_brief_index INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE students ADD COLUMN current_step_index INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def ensure_student(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO students (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, last_name),
    )
    cur.execute(
        "UPDATE students SET username=?, first_name=?, last_name=? WHERE user_id=?",
        (username, first_name, last_name, user_id),
    )
    conn.commit()
    conn.close()


def set_selected_brief(user_id: int, brief_index: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE students SET selected_brief_index = ? WHERE user_id = ?", (brief_index, user_id))
    conn.commit()
    conn.close()


def get_selected_brief(user_id: int) -> int | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT selected_brief_index FROM students WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else None


def clear_selected_brief(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE students SET selected_brief_index = NULL, current_step_index = NULL WHERE user_id = ?",
        (user_id,),
    )
    cur.execute("DELETE FROM checklist_progress WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def clear_checklist_progress(user_id: int) -> int:
    """Удаляет все отметки чеклиста для пользователя. Возвращает количество удалённых строк."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM checklist_progress WHERE user_id = ?", (user_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def mark_brief_done(user_id: int, brief_index: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO progress (user_id, brief_index) VALUES (?, ?)",
        (user_id, brief_index),
    )
    conn.commit()
    conn.close()


def get_progress(user_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT brief_index FROM progress WHERE user_id = ? ORDER BY brief_index", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_help_request(user_id: int, kind: str, comment: str = ""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO help_requests (user_id, kind, comment) VALUES (?, ?, ?)",
        (user_id, kind, comment),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_all_students_with_progress():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.user_id, s.username, s.first_name, s.last_name,
               (SELECT COUNT(*) FROM progress p WHERE p.user_id = s.user_id) AS completed_count
        FROM students s
        ORDER BY s.first_name, s.last_name
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {"user_id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3], "completed_count": r[4]}
        for r in rows
    ]


def get_help_requests(resolved: bool = False):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT hr.id, hr.user_id, hr.kind, hr.comment, hr.created_at, hr.resolved,
               s.username, s.first_name, s.last_name
        FROM help_requests hr
        JOIN students s ON s.user_id = hr.user_id
        WHERE hr.resolved = ?
        ORDER BY hr.created_at DESC
    """, (1 if resolved else 0,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "user_id": r[1], "kind": r[2], "comment": r[3],
            "created_at": r[4], "resolved": r[5],
            "username": r[6], "first_name": r[7], "last_name": r[8],
        }
        for r in rows
    ]


def resolve_help_request(request_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE help_requests SET resolved = 1 WHERE id = ?", (request_id,))
    conn.commit()
    conn.close()


# --- Чеклист: отметки студентов ---


def set_checklist_item(user_id: int, brief_index: int, item_index: int, completed: bool):
    conn = get_connection()
    cur = conn.cursor()
    if completed:
        cur.execute(
            "INSERT OR REPLACE INTO checklist_progress (user_id, brief_index, item_index) VALUES (?, ?, ?)",
            (user_id, brief_index, item_index),
        )
    else:
        cur.execute(
            "DELETE FROM checklist_progress WHERE user_id = ? AND brief_index = ? AND item_index = ?",
            (user_id, brief_index, item_index),
        )
    conn.commit()
    conn.close()


def get_checklist_checked(user_id: int, brief_index: int) -> set:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT item_index FROM checklist_progress WHERE user_id = ? AND brief_index = ?",
        (user_id, brief_index),
    )
    rows = cur.fetchall()
    conn.close()
    return {r[0] for r in rows}


def set_current_step(user_id: int, step_index: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE students SET current_step_index = ? WHERE user_id = ?",
        (step_index, user_id),
    )
    conn.commit()
    conn.close()


def get_current_step(user_id: int) -> int | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT current_step_index FROM students WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else None


def get_all_checklist_results():
    """Для админа: (user_id, brief_index, total_items, completed_count), с именами из students."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.user_id, s.first_name, s.last_name, s.username, s.selected_brief_index,
               (SELECT COUNT(*) FROM checklist_progress cp WHERE cp.user_id = s.user_id AND cp.brief_index = s.selected_brief_index) AS completed
        FROM students s
        WHERE s.selected_brief_index IS NOT NULL
        ORDER BY s.first_name, s.last_name
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {"user_id": r[0], "first_name": r[1], "last_name": r[2], "username": r[3], "brief_index": r[4], "completed_count": r[5]}
        for r in rows
    ]
