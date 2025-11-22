import os
import time
import requests
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import threading

# -------------------- ЗАГРУЗКА .env --------------------

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

if not load_dotenv(ENV_PATH):
    print(f"⚠️ Файл .env не найден по пути {ENV_PATH}. Читаю только системные переменные.")

def _env(name):
    value = os.getenv(name)
    return value.strip() if value else None

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
COC_API_TOKEN = _env("COC_API_TOKEN")
CLAN_TAG = _env("CLAN_TAG")
GROUP_CHAT_ID = _env("GROUP_CHAT_ID")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("ERROR: TELEGRAM_BOT_TOKEN не найден в .env")

if not COC_API_TOKEN:
    print("⚠️ COC_API_TOKEN не найден — команды войны работать не будут.")

if not CLAN_TAG:
    print("⚠️ CLAN_TAG не найден — команды войны работать не будут.")

BASE_TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
if GROUP_CHAT_ID:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID)
CLAN_TAG_ENCODED = requests.utils.quote(CLAN_TAG) if CLAN_TAG else ""
COC_URL = f"https://api.clashofclans.com/v1/clans/{CLAN_TAG_ENCODED}/currentwar" if CLAN_TAG_ENCODED else ""
PLAYER_URL = "https://api.clashofclans.com/v1/players/"

COC_HEADERS = {"Authorization": f"Bearer {COC_API_TOKEN}"} if COC_API_TOKEN else {}

# -------------------- АВТОУДАЛЕНИЕ СООБЩЕНИЙ --------------------

AUTO_DELETE_SECONDS = 8 * 60
MENU_INACTIVITY_SECONDS = 6 * 60

_tracked_messages = []
_tracked_lock = threading.Lock()


def _extract_message_id(response):
    try:
        data = response.json()
        return data.get("result", {}).get("message_id")
    except Exception:
        return None


def _track_message(chat_id, message_id, kind):
    if not message_id:
        return
    now = time.time()
    with _tracked_lock:
        _tracked_messages[:] = [
            m for m in _tracked_messages
            if not (m["chat_id"] == chat_id and m["message_id"] == message_id)
        ]
        _tracked_messages.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "type": kind,  # "menu" или "reply"
            "created_at": now,
            "last_activity": now,
        })


def _touch_menu(chat_id, message_id):
    if not message_id:
        return
    now = time.time()
    with _tracked_lock:
        for m in _tracked_messages:
            if m["chat_id"] == chat_id and m["message_id"] == message_id and m["type"] == "menu":
                m["last_activity"] = now
                break


def _touch_all_menus(chat_id):
    now = time.time()
    with _tracked_lock:
        for m in _tracked_messages:
            if m["chat_id"] == chat_id and m["type"] == "menu":
                m["last_activity"] = now


def delete_message(chat_id, message_id):
    try:
        requests.post(
            BASE_TELEGRAM_URL + "/deleteMessage",
            data={"chat_id": chat_id, "message_id": message_id},
            timeout=5,
        )
    except Exception:
        pass


def auto_cleanup_worker():
    while True:
        now = time.time()
        to_delete = []
        with _tracked_lock:
            keep = []
            for m in _tracked_messages:
                if m["type"] == "menu":
                    if now - m["last_activity"] >= MENU_INACTIVITY_SECONDS:
                        to_delete.append(m)
                    else:
                        keep.append(m)
                else:
                    if now - m["created_at"] >= AUTO_DELETE_SECONDS:
                        to_delete.append(m)
                    else:
                        keep.append(m)
            _tracked_messages[:] = keep

        for m in to_delete:
            delete_message(m["chat_id"], m["message_id"])

        time.sleep(30)

# -------------------- ЛОКАЛЬНАЯ БД (в одном файле) --------------------

DB_PATH = "links.db"

def db_init():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            coc_tag TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            sub_type TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def db_save_link(telegram_id, coc_tag):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO links (telegram_id, coc_tag)
        VALUES (?, ?)
    """, (str(telegram_id), coc_tag))
    conn.commit()
    conn.close()

def db_get_link(telegram_id):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT coc_tag FROM links WHERE telegram_id=?", (str(telegram_id),))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows] if rows else []

def db_get_all_links_for_user(telegram_id):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT coc_tag FROM links WHERE telegram_id=?", (str(telegram_id),))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows] if rows else []

def db_get_all_links():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT telegram_id, coc_tag FROM links")
    rows = cur.fetchall()
    conn.close()
    return rows

# -------------------- SUBSCRIPTION HELPERS --------------------
def db_add_sub(telegram_id, sub_type):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO subs (telegram_id, sub_type) VALUES (?, ?)", (str(telegram_id), sub_type))
    conn.commit()
    conn.close()

def db_get_subs():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT telegram_id, sub_type FROM subs")
    rows = cur.fetchall()
    conn.close()
    return rows

# -------------------- SUBSCRIPTION CLEAR HELPER --------------------
def db_clear_subs_for_user(telegram_id):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM subs WHERE telegram_id=?", (str(telegram_id),))
    conn.commit()
    conn.close()

# -------------------- УТИЛИТЫ --------------------

def normalize_tag(tag):
    tag = (tag or "").strip().upper()
    if tag and not tag.startswith("#"):
        tag = "#" + tag
    return tag

def send_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "📁 Война"} ],
            [ {"text": "📁 ЛВК"} ],
            [ {"text": "📁 Аккаунты"} ],
            [ {"text": "📁 Подписки"} ],
            [ {"text": "📁 Проверка"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Главное меню:",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


# -------------------- INLINE MENUS --------------------

def send_main_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "📁 Война", "callback_data": "menu_war"} ],
            [ {"text": "📁 ЛВК", "callback_data": "menu_lwk"} ],
            [ {"text": "📁 Аккаунты", "callback_data": "menu_accounts"} ],
            [ {"text": "📁 Подписки", "callback_data": "menu_subs"} ],
            [ {"text": "📁 Проверка", "callback_data": "menu_check"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Главное меню:",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_war_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "📊 Статус войны", "callback_data": "war_status"} ],
            [ {"text": "⏳ До конца", "callback_data": "war_left"}, {"text": "❗ Кто не бил", "callback_data": "war_missed"} ],
            [ {"text": "⚔ Мои атаки", "callback_data": "war_myattacks"} ],
            [ {"text": "📈 Текущая война", "callback_data": "war_current"} ],
            [ {"text": "🔙 Назад", "callback_data": "back_main"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Раздел: Война",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_lwk_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "📊 Статус ЛВК", "callback_data": "lwk_status"} ],
            [ {"text": "⏳ До конца ЛВК", "callback_data": "lwk_end"}, {"text": "❗ Кто не бил ЛВК", "callback_data": "lwk_missed"} ],
            [ {"text": "🔥 Прогресс ЛВК", "callback_data": "lwk_progress"}, {"text": "⭐ Сравнение звёзд", "callback_data": "lwk_compare"} ],
            [ {"text": "📋 Раунды ЛВК", "callback_data": "lwk_rounds"}, {"text": "📈 Таблица ЛВК", "callback_data": "lwk_table"} ],
            [ {"text": "⚔ Мои атаки ЛВК", "callback_data": "lwk_myattacks"} ],
            [ {"text": "🔙 Назад", "callback_data": "back_main"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Раздел: Лига Воин Кланов (ЛВК)",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_accounts_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "👤 Мои аккаунты", "callback_data": "acc_mine"} ],
            [ {"text": "➕ Добавить аккаунт", "callback_data": "acc_add"} ],
            [ {"text": "✅ Проверка привязок", "callback_data": "acc_check"} ],
            [ {"text": "🔙 Назад", "callback_data": "back_main"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Раздел: Аккаунты",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_subscriptions_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "🔔 Напомнить о начале войны", "callback_data": "sub_war_start"} ],
            [ {"text": "⏳ За 3 часа (обычная война)", "callback_data": "sub_war_3h"} ],
            [ {"text": "⏳ За 3 часа (ЛВК)", "callback_data": "sub_lwk_3h"} ],
            [ {"text": "🔕 Выключить подписки", "callback_data": "sub_disable"} ],
            [ {"text": "🔙 Назад", "callback_data": "back_main"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Раздел: Подписки",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_check_menu_inline(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [ {"text": "✅ Проверить привязки", "callback_data": "check_run"} ],
            [ {"text": "🔙 Назад", "callback_data": "back_main"} ],
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": "Раздел: Проверка",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        if message_id:
            payload["message_id"] = message_id
            requests.post(
                BASE_TELEGRAM_URL + "/editMessageText",
                data=payload,
                timeout=5,
            )
            _touch_menu(chat_id, message_id)
        else:
            resp = requests.post(
                BASE_TELEGRAM_URL + "/sendMessage",
                data=payload,
                timeout=5,
            )
            message_id = _extract_message_id(resp)
            _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


# -------------------- SUBMENU KEYBOARDS --------------------

def send_war_menu(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "📊 Статус войны"} ],
            [ {"text": "⏳ До конца"}, {"text": "❗ Кто не бил"} ],
            [ {"text": "⚔ Мои атаки"} ],
            [ {"text": "📈 Текущая война"} ],
            [ {"text": "🔙 Назад"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Раздел: Война",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


def send_accounts_menu(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "👤 Мои аккаунты"} ],
            [ {"text": "➕ Добавить аккаунт"} ],
            [ {"text": "✅ Проверка привязок"} ],
            [ {"text": "🔙 Назад"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Раздел: Аккаунты",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass


# -------------------- ЛВК МЕНЮ --------------------

def send_lwk_menu(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "📊 Статус ЛВК"} ],
            [ {"text": "⏳ До конца ЛВК"}, {"text": "❗ Кто не бил ЛВК"} ],
            [ {"text": "🔥 Прогресс ЛВК"}, {"text": "⭐ Сравнение звёзд"} ],
            [ {"text": "📋 Раунды ЛВК"}, {"text": "📈 Таблица ЛВК"} ],
            [ {"text": "⚔ Мои атаки ЛВК"} ],
            [ {"text": "🔙 Назад"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Раздел: Лига Воин Кланов (ЛВК)",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass

# -------------------- SUBSCRIPTIONS MENU --------------------
def send_subscriptions_menu(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "🔔 Напомнить о начале войны"} ],
            [ {"text": "⏳ Напомнить за 3 часа (обычная война)"} ],
            [ {"text": "⏳ Напомнить за 3 часа (ЛВК)"} ],
            [ {"text": "🔕 Выключить подписки"} ],
            [ {"text": "🔙 Назад"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Раздел: Подписки",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass

def send_check_menu(chat_id):
    keyboard = {
        "keyboard": [
            [ {"text": "✅ Проверить привязки"} ],
            [ {"text": "🔙 Назад"} ]
        ],
        "resize_keyboard": True
    }
    try:
        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": "Раздел: Проверка",
                "reply_markup": json.dumps(keyboard)
            },
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        _track_message(chat_id, message_id, "menu")
    except Exception:
        pass

def send_message(chat_id, text, *, parse_mode="Markdown", auto_delete=True, reply_markup=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup if isinstance(reply_markup, str) else json.dumps(reply_markup)

        resp = requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data=payload,
            timeout=5,
        )
        message_id = _extract_message_id(resp)
        if auto_delete:
            _track_message(chat_id, message_id, "reply")
        return message_id
    except Exception:
        return None

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(BASE_TELEGRAM_URL + "/getUpdates", params=params, timeout=35)
        data = r.json()
        return data.get("result", [])
    except:
        return []

# -------------------- Clash of Clans API --------------------

def get_war():
    if not COC_API_TOKEN or not COC_URL:
        return None
    try:
        r = requests.get(COC_URL, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_player(tag):
    if not COC_API_TOKEN:
        return None
    url = PLAYER_URL + requests.utils.quote(tag)
    try:
        r = requests.get(url, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_clan():
    if not COC_API_TOKEN or not CLAN_TAG_ENCODED:
        return None
    try:
        url = f"https://api.clashofclans.com/v1/clans/{CLAN_TAG_ENCODED}"
        r = requests.get(url, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def parse_time(s):
    try:
        s = s.replace(".000Z", "")
        return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except:
        return None

def format_diff(dt):
    if not dt:
        return "?"
    now = datetime.now(timezone.utc)
    diff = dt - now
    if diff.total_seconds() <= 0:
        return "0ч 0м"
    hours = diff.seconds // 3600 + diff.days * 24
    minutes = (diff.seconds % 3600) // 60
    return f"{hours}ч {minutes}м"

# -------------------- Clash of Clans API: ЛВК --------------------

def get_lwk_group():
    """Получает список раундов ЛВК и warTag для каждой войны."""
    if not COC_API_TOKEN or not COC_URL:
        return None
    try:
        url = f"https://api.clashofclans.com/v1/clans/{CLAN_TAG_ENCODED}/currentwar/leaguegroup"
        r = requests.get(url, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_lwk_war(war_tag):
    """Получает данные одной ЛВК-войны по warTag."""
    try:
        war_tag_encoded = requests.utils.quote(war_tag)
        url = f"https://api.clashofclans.com/v1/clanwarleagues/wars/{war_tag_encoded}"
        r = requests.get(url, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_current_lwk_war():
    """Возвращает текущую ЛВК-войну (state == 'inWar')."""
    group = get_lwk_group()
    if not group or "rounds" not in group:
        return None

    for rnd in group["rounds"]:
        for war_tag in rnd.get("warTags", []):
            if war_tag == "#0":  # пустой слот
                continue
            w = get_lwk_war(war_tag)
            if not w:
                continue
            if w.get("state") == "inWar":
                return w
    return None

# -------------------- КОМАНДЫ --------------------

def cmd_start(chat_id):
    send_message(chat_id,
        "👋 Привет! Клан *Legion*.\n\n"
        "Выберите действие ниже:"
    )
    send_main_menu_inline(chat_id)

def cmd_warstatus(chat_id):
    w = get_war()
    if not w:
        send_message(chat_id, "Не удалось получить данные о войне.")
        return
    state = w.get("state", "?")
    msg = (
        "📊 **Статус войны**\n\n"
        f"🏷️ **Этап:** {state}\n"
    )
    if "startTime" in w:
        start = parse_time(w["startTime"])
        msg += f"⏳ **До начала:** {format_diff(start)}\n"
    if "endTime" in w:
        end = parse_time(w["endTime"])
        msg += f"⏳ **До конца:** {format_diff(end)}\n"
    send_message(chat_id, msg)

def cmd_warstart(chat_id):
    w = get_war()
    if not w:
        send_message(chat_id, "Ошибка загрузки войны.")
        return
    if w["state"] != "preparation":
        send_message(chat_id, f"Сейчас не подготовка. Статус: {w['state']}")
        return
    dt = parse_time(w["startTime"])
    send_message(chat_id, f"⏳ До начала войны: {format_diff(dt)}")

def cmd_warend(chat_id):
    w = get_war()
    if not w:
        send_message(chat_id, "Ошибка загрузки войны.")
        return
    if w["state"] != "inWar":
        send_message(chat_id, f"Сейчас не этап атаки. Статус: {w['state']}")
        return
    dt = parse_time(w["endTime"])
    send_message(chat_id, f"⏳ До конца войны: {format_diff(dt)}")

def cmd_warleft(chat_id):
    w = get_war()
    if not w or w["state"] != "inWar":
        send_message(chat_id, "Война сейчас не идёт.")
        return
    size = w.get("teamSize", 0)
    total = size * 2
    attacks = w["clan"].get("attacks", 0)
    left = total - attacks
    send_message(chat_id, f"⚔ Осталось атак: {left}\nВыполнено: {attacks}/{total}")


# -------------------- ТЕКУЩАЯ ВОЙНА --------------------
def cmd_war_current(chat_id):
    w = get_war()
    if not w or w.get("state") not in ("inWar", "warEnded", "preparation"):
        send_message(chat_id, "Не удалось получить данные о текущей войне.")
        return
    clan = w.get("clan", {})
    opp = w.get("opponent", {})
    clan_name = clan.get("name", "Наш клан")
    opp_name = opp.get("name", "Клан врага")
    clan_stars = clan.get("stars", 0)
    opp_stars = opp.get("stars", 0)
    clan_atk = clan.get("attacks", 0)
    opp_atk = opp.get("attacks", 0)
    msg = (
        "📈 **Текущая война**\n\n"
        f"{clan_name} — {opp_name}\n"
        f"⭐ {clan_stars} — {opp_stars}\n"
        f"⚔ {clan_atk} атак — {opp_atk} атак\n"
    )
    send_message(chat_id, msg)

def cmd_missed(chat_id):
    w = get_war()
    if not w or w["state"] != "inWar":
        send_message(chat_id, "Война не идёт.")
        return
    msg = "❗ **Не сделали атаку:**\n\n"
    missed = []
    for m in w["clan"]["members"]:
        if not m.get("attacks"):
            missed.append(f"{m['name']} — {m['tag']}")
    if not missed:
        send_message(chat_id, "🎉 Все сделали хотя бы одну атаку!")
    else:
        send_message(chat_id, msg + "\n".join(missed))

def cmd_link(chat_id, user_id, text):
    parts = text.split()
    if len(parts) < 2:
        send_message(chat_id, "Использование: /link #TAG")
        return
    tag_raw = parts[1].strip()
    if not tag_raw.startswith("#"):
        send_message(chat_id, "Тег должен начинаться с #")
        return
    tag = normalize_tag(tag_raw)
    db_save_link(user_id, tag)
    player = get_player(tag)
    player_name = player.get("name", tag) if player else tag
    send_message(chat_id, f"🔗 Аккаунт {player_name} добавлен.")

def cmd_mytag(chat_id, user_id):
    tags = db_get_all_links_for_user(user_id)
    if not tags:
        send_message(chat_id, "У вас нет привязанных профилей. Используйте /link #TAG")
        return
    msg = "Ваши аккаунты:\n\n"
    for t in tags:
        p = get_player(t)
        name = p.get("name", t) if p else t
        msg += f"• {name} — {t}\n"
    send_message(chat_id, msg)

def cmd_myattacks(chat_id, user_id):
    tags = db_get_all_links_for_user(user_id)
    if not tags:
        send_message(chat_id, "Сначала привяжите аккаунт через /link #TAG")
        return
    w = get_war()
    if not w or w["state"] != "inWar":
        send_message(chat_id, "Сейчас нет войны.")
        return
    members = w["clan"]["members"]
    msg = "⚔ **Ваши атаки:**\n\n"
    found_any = False
    for tag in tags:
        me = next((m for m in members if m["tag"] == tag), None)
        if not me:
            continue
        found_any = True
        pname = me["name"]
        if not me.get("attacks"):
            msg += f"• {pname}: нет атак\n"
        else:
            msg += f"• {pname}:\n"
            for atk in me["attacks"]:
                msg += f"    ⭐ {atk['stars']} — {atk['destructionPercentage']}%\n"
    if not found_any:
        msg = "Ни один из ваших аккаунтов не участвует в этой войне."
    send_message(chat_id, msg)

def is_user_in_group(user_id):
    if not GROUP_CHAT_ID:
        return True
    try:
        r = requests.get(
            BASE_TELEGRAM_URL + "/getChatMember",
            params={"chat_id": GROUP_CHAT_ID, "user_id": user_id},
            timeout=5,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        status = data.get("result", {}).get("status")
        return status in ("creator", "administrator", "member", "restricted")
    except Exception:
        return False

def cmd_check_links(chat_id):
    clan = get_clan()
    if not clan or "memberList" not in clan:
        send_message(chat_id, "Не удалось получить список клана. Проверьте CLAN_TAG и COC_API_TOKEN.")
        return

    members = clan.get("memberList", [])
    links = db_get_all_links()

    tag_to_users = {}
    for uid, tag in links:
        norm_tag = normalize_tag(tag)
        if not norm_tag:
            continue
        tag_to_users.setdefault(norm_tag, set()).add(uid)

    missing_links = []
    missing_group = []

    for member in members:
        tag = normalize_tag(member.get("tag"))
        name = member.get("name", tag)
        linked_users = tag_to_users.get(tag)

        if not linked_users:
            missing_links.append(f"{name} — {tag}")
            continue

        if GROUP_CHAT_ID:
            in_group = any(is_user_in_group(uid) for uid in linked_users)
            if not in_group:
                missing_group.append(f"{name} — {tag}")

    msg = "✅ Проверка привязок\n\n"
    msg += f"Игроков в клане: {len(members)}\n"
    msg += f"Уникальных привязанных тегов: {len(tag_to_users)}\n"

    if missing_links:
        msg += "\n❗ Нет привязки:\n" + "\n".join(missing_links)

    if GROUP_CHAT_ID and missing_group:
        msg += "\n\n🚪 Вне группы (но привязаны):\n" + "\n".join(missing_group)

    if not missing_links and (not GROUP_CHAT_ID or not missing_group):
        tail = " и состоят в группе" if GROUP_CHAT_ID else ""
        msg += f"\nВсе игроки привязали теги{tail}."

    send_message(chat_id, msg, auto_delete=False)

# -------------------- ОБРАБОТКА --------------------

def cmd_lwk_status(chat_id):
    w = get_current_lwk_war()
    if not w:
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    state = w.get("state", "?")
    end = parse_time(w["endTime"]) if "endTime" in w else None
    msg = (
        "📊 **Статус ЛВК**\n\n"
        f"🏷️ **Этап:** {state}\n"
        f"⏳ **До конца:** {format_diff(end)}\n"
    )
    send_message(chat_id, msg)

def cmd_lwk_end(chat_id):
    w = get_current_lwk_war()
    if not w:
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    dt = parse_time(w["endTime"])
    send_message(chat_id, f"⏳ До конца ЛВК: {format_diff(dt)}")

def cmd_lwk_missed(chat_id):
    w = get_current_lwk_war()
    if not w or w.get("state") != "inWar":
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    msg = "❗ Не сделали атаку в ЛВК:\n\n"
    missed = []
    for m in w["clan"]["members"]:
        if not m.get("attacks"):
            missed.append(f"{m['name']} — {m['tag']}")
    if not missed:
        send_message(chat_id, "🎉 Все сделали атаки в ЛВК!")
    else:
        send_message(chat_id, msg + "\n".join(missed))

def cmd_lwk_myattacks(chat_id, user_id):
    tags = db_get_all_links_for_user(user_id)
    if not tags:
        send_message(chat_id, "Привяжите аккаунт через /link #TAG")
        return
    w = get_current_lwk_war()
    if not w or w.get("state") != "inWar":
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    msg = "Ваши атаки в ЛВК:\n\n"
    found = False
    for t in tags:
        me = next((m for m in w["clan"]["members"] if m["tag"] == t), None)
        if not me:
            continue
        found = True
        pname = me["name"]
        if not me.get("attacks"):
            msg += f"• {pname}: нет атак\n"
        else:
            msg += f"• {pname}:\n"
            for atk in me["attacks"]:
                msg += f"    ⭐ {atk['stars']} — {atk['destructionPercentage']}%\n"
    if not found:
        msg = "Ни один из ваших аккаунтов не участвует в текущей ЛВК-войне."
    send_message(chat_id, msg)

# -------------------- NEW LWK COMMANDS --------------------

def cmd_lwk_progress(chat_id):
    w = get_current_lwk_war()
    if not w or w.get("state") != "inWar":
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    clan = w.get("clan", {})
    size = w.get("teamSize", 0)
    max_attacks = size * 2
    done = clan.get("attacks", 0)
    msg = (
        "🔥 **Прогресс ЛВК**\n\n"
        f"⭐ **Звёзды:** {clan.get('stars',0)}\n"
        f"⚔ **Атаки:** {done}/{max_attacks}\n"
        f"🔨 **Разрушение:** {clan.get('destructionPercentage',0)}%\n"
    )
    send_message(chat_id, msg)

def cmd_lwk_compare(chat_id):
    w = get_current_lwk_war()
    if not w:
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    clan = w.get("clan", {})
    opp = w.get("opponent", {})
    msg = (
        "⭐ **Сравнение звёзд**\n\n"
        f"{clan.get('name','Наш клан')} — {clan.get('stars',0)}⭐\n"
        f"{opp.get('name','Противник')} — {opp.get('stars',0)}⭐\n\n"
        f"💥 **Разница:** {clan.get('stars',0) - opp.get('stars',0)}"
    )
    send_message(chat_id, msg)

def cmd_lwk_rounds(chat_id):
    group = get_lwk_group()
    if not group or "rounds" not in group:
        send_message(chat_id, "Не удалось получить раунды ЛВК.")
        return
    msg = "📋 **Раунды ЛВК**\n\n"
    for i, rnd in enumerate(group["rounds"], start=1):
        msg += f"Раунд {i}:\n"
        for tag in rnd.get("warTags", []):
            msg += f" • {tag}\n"
        msg += "\n"
    send_message(chat_id, msg)

def cmd_lwk_table(chat_id):
    w = get_current_lwk_war()
    if not w:
        send_message(chat_id, "ЛВК-война сейчас не идёт.")
        return
    clan = w.get("clan", {})
    opp = w.get("opponent", {})
    msg = (
        "📈 **Таблица ЛВК**\n\n"
        f"{clan.get('name','Наш клан')}:\n"
        f"⭐ {clan.get('stars',0)}\n"
        f"🔨 {clan.get('destructionPercentage',0)}%\n\n"
        f"{opp.get('name','Противник')}:\n"
        f"⭐ {opp.get('stars',0)}\n"
        f"🔨 {opp.get('destructionPercentage',0)}%\n"
    )
    send_message(chat_id, msg)


# -------------------- INLINE CALLBACK HANDLER --------------------

def handle_callback(cq):
    message = cq.get("message")
    if not message:
        return
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    data = cq.get("data")
    user = cq.get("from", {})
    user_id = user.get("id")

    print(f"[CALLBACK] chat={chat_id} data={data}")
    _touch_menu(chat_id, message_id)
    _touch_all_menus(chat_id)

    # Меню навигации
    if data == "menu_war":
        send_war_menu_inline(chat_id, message_id)
    elif data == "menu_lwk":
        send_lwk_menu_inline(chat_id, message_id)
    elif data == "menu_accounts":
        send_accounts_menu_inline(chat_id, message_id)
    elif data == "menu_subs":
        send_subscriptions_menu_inline(chat_id, message_id)
    elif data == "menu_check":
        send_check_menu_inline(chat_id, message_id)
    elif data == "back_main":
        send_main_menu_inline(chat_id, message_id)

    # Обычная война
    elif data == "war_status":
        cmd_warstatus(chat_id)
    elif data == "war_left":
        cmd_warleft(chat_id)
    elif data == "war_missed":
        cmd_missed(chat_id)
    elif data == "war_myattacks":
        if user_id:
            cmd_myattacks(chat_id, user_id)
    elif data == "war_current":
        cmd_war_current(chat_id)

    # ЛВК
    elif data == "lwk_status":
        cmd_lwk_status(chat_id)
    elif data == "lwk_end":
        cmd_lwk_end(chat_id)
    elif data == "lwk_missed":
        cmd_lwk_missed(chat_id)
    elif data == "lwk_progress":
        cmd_lwk_progress(chat_id)
    elif data == "lwk_compare":
        cmd_lwk_compare(chat_id)
    elif data == "lwk_rounds":
        cmd_lwk_rounds(chat_id)
    elif data == "lwk_table":
        cmd_lwk_table(chat_id)
    elif data == "lwk_myattacks":
        if user_id:
            cmd_lwk_myattacks(chat_id, user_id)

    # Аккаунты
    elif data == "acc_mine":
        if user_id:
            cmd_mytag(chat_id, user_id)
    elif data == "acc_add":
        send_message(chat_id, "Введите команду в формате: /link #TAG")
    elif data == "acc_check":
        cmd_check_links(chat_id)

    # Подписки
    elif data == "sub_war_start":
        if user_id:
            db_add_sub(user_id, "war_start")
            send_message(chat_id, "Подписка добавлена: уведомить о начале войны.")
    elif data == "sub_war_3h":
        if user_id:
            db_add_sub(user_id, "war_3h")
            send_message(chat_id, "Подписка добавлена: уведомить за 3 часа до конца обычной войны.")
    elif data == "sub_lwk_3h":
        if user_id:
            db_add_sub(user_id, "lwk_3h")
            send_message(chat_id, "Подписка добавлена: уведомить за 3 часа до конца ЛВК.")
    elif data == "sub_disable":
        if user_id:
            db_clear_subs_for_user(user_id)
            send_message(chat_id, "🔕 Все ваши подписки отключены.")

    # Проверка
    elif data == "check_run":
        cmd_check_links(chat_id)


def handle_update(update):
    # Обработка callback-кнопок
    cq = update.get("callback_query")
    if cq:
        handle_callback(cq)
        return
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    # --- FILTER GROUP CHAT NOISE ---
    chat = msg.get("chat", {})
    chat_type = chat.get("type")

    # react only if command or bot mention
    text_raw = msg.get("text", "") or ""
    bot_username = os.getenv("BOT_USERNAME", "").lower()

    if chat_type in ("group", "supergroup"):
        lower = text_raw.lower()
        # react ONLY to slash commands in group chats
        if not lower.startswith("/"):
            return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    user_id = msg["from"]["id"]
    _touch_all_menus(chat_id)

    print(f"[UPDATE] chat={chat_id} text={text}")

    if text == "/start":
        cmd_start(chat_id)
    elif text == "/menu":
        cmd_start(chat_id)
    elif text == "📁 Война":
        send_war_menu(chat_id)
    elif text == "📁 ЛВК":
        send_lwk_menu(chat_id)
    elif text == "📊 Статус ЛВК":
        cmd_lwk_status(chat_id)
    elif text == "⏳ До конца ЛВК":
        cmd_lwk_end(chat_id)
    elif text == "❗ Кто не бил ЛВК":
        cmd_lwk_missed(chat_id)
    elif text == "⚔ Мои атаки ЛВК":
        cmd_lwk_myattacks(chat_id, user_id)
    elif text == "🔥 Прогресс ЛВК":
        cmd_lwk_progress(chat_id)
    elif text == "⭐ Сравнение звёзд":
        cmd_lwk_compare(chat_id)
    elif text == "📋 Раунды ЛВК":
        cmd_lwk_rounds(chat_id)
    elif text == "📈 Таблица ЛВК":
        cmd_lwk_table(chat_id)
    elif text == "📁 Аккаунты":
        send_accounts_menu(chat_id)
    elif text == "📁 Подписки":
        send_subscriptions_menu(chat_id)
    elif text == "📁 Проверка":
        send_check_menu(chat_id)
    elif text == "🔔 Напомнить о начале войны":
        db_add_sub(user_id, "war_start")
        send_message(chat_id, "Подписка добавлена: уведомить о начале войны.")
    elif text == "⏳ Напомнить за 3 часа (обычная война)":
        db_add_sub(user_id, "war_3h")
        send_message(chat_id, "Подписка добавлена: уведомить за 3 часа до конца обычной войны.")
    elif text == "⏳ Напомнить за 3 часа (ЛВК)":
        db_add_sub(user_id, "lwk_3h")
        send_message(chat_id, "Подписка добавлена: уведомить за 3 часа до конца ЛВК.")
    elif text == "🔕 Выключить подписки":
        db_clear_subs_for_user(user_id)
        send_message(chat_id, "🔕 Все ваши подписки отключены.")
    elif text == "🔙 Назад":
        send_keyboard(chat_id)
    elif text == "📊 Статус войны":
        cmd_warstatus(chat_id)
    elif text == "⏳ До конца":
        cmd_warleft(chat_id)
    elif text == "👤 Мои аккаунты":
        cmd_mytag(chat_id, user_id)
    elif text == "✅ Проверка привязок":
        cmd_check_links(chat_id)
    elif text == "✅ Проверить привязки":
        cmd_check_links(chat_id)
    elif text == "⚔ Мои атаки":
        cmd_myattacks(chat_id, user_id)
    elif text == "📈 Текущая война":
        cmd_war_current(chat_id)
    elif text == "❗ Кто не бил":
        cmd_missed(chat_id)
    elif text == "➕ Добавить аккаунт":
        send_message(chat_id, "Введите команду в формате: /link #TAG")
    elif text == "/warstatus":
        cmd_warstatus(chat_id)
    elif text == "/warstart":
        cmd_warstart(chat_id)
    elif text == "/warend":
        cmd_warend(chat_id)
    elif text == "/warleft":
        cmd_warleft(chat_id)
    elif text == "/warcurrent":
        cmd_war_current(chat_id)
    elif text == "/missed":
        cmd_missed(chat_id)
    elif text == "/checklinks":
        cmd_check_links(chat_id)
    elif text.startswith("/link"):
        cmd_link(chat_id, user_id, text)
    elif text == "/mytag":
        cmd_mytag(chat_id, user_id)
    elif text == "/myattacks":
        cmd_myattacks(chat_id, user_id)
    else:
        send_message(chat_id, "Не знаю такую команду. Используйте /start")


# -------------------- BACKGROUND NOTIFIER --------------------

notified_lwk = False
notified_war = False
notified_war_start = False

def background_notifier():
    global notified_lwk
    while True:
        try:
            w = get_current_lwk_war()
            if w and w.get("state") == "inWar":
                end = parse_time(w.get("endTime"))
                if end:
                    now = datetime.now(timezone.utc)
                    diff_hours = (end - now).total_seconds() / 3600

                    # Notify once at 3h remaining
                    if diff_hours <= 3 and not notified_lwk and diff_hours > 0:
                        notified_lwk = True

                        # Build missed list
                        missed = []
                        for m in w["clan"]["members"]:
                            if not m.get("attacks"):
                                missed.append(f"{m['name']} — {m['tag']}")

                        message = "⏳ *До конца ЛВК осталось 3 часа!*\n\n"
                        if missed:
                            message += "❗ Не били:\n" + "\n".join(missed)
                        else:
                            message += "🎉 Все уже атаковали!"

                        # Broadcast to all linked users
                        import sqlite3
                        conn = sqlite3.connect(DB_PATH)
                        cur = conn.execute("SELECT DISTINCT telegram_id FROM links")
                        rows = cur.fetchall()
                        conn.close()

                        for row in rows:
                            chat_id = row[0]
                            send_message(chat_id, message, auto_delete=False)

                        # Send to group chat if configured
                        if GROUP_CHAT_ID:
                            send_message(GROUP_CHAT_ID, message, auto_delete=False)

                    # Reset flag for next war
                    if diff_hours > 3:
                        notified_lwk = False

            # LWK war ended notification (notify once)
            global notified_lwk_end
            if not 'notified_lwk_end' in globals():
                notified_lwk_end = False

            if w and w.get("state") == "warEnded" and not notified_lwk_end:
                notified_lwk_end = True

                clan = w.get("clan", {})
                opp = w.get("opponent", {})

                clan_name = clan.get("name", "Наш клан")
                opp_name = opp.get("name", "Противник")

                clan_stars = clan.get("stars", 0)
                opp_stars = opp.get("stars", 0)

                clan_atk = clan.get("attacks", 0)
                opp_atk = opp.get("attacks", 0)

                result = "Ничья"
                if clan_stars > opp_stars:
                    result = "🏆 Победа!"
                elif clan_stars < opp_stars:
                    result = "❌ Поражение"

                missed = []
                for m in clan.get("members", []):
                    if not m.get("attacks"):
                        missed.append(f"{m['name']} — {m['tag']}")

                msg = (
                    "🔔 *ЛВК-война завершилась!*\n\n"
                    f"{clan_name} — {opp_name}\n"
                    f"⭐ {clan_stars} — {opp_stars}\n"
                    f"⚔ {clan_atk} атак — {opp_atk} атак\n\n"
                    f"🏁 Итог: *{result}*\n\n"
                )

                if missed:
                    msg += "❗ *Не атаковали:*\n" + "\n".join(missed)

                # broadcast to all linked users
                import sqlite3
                conn = sqlite3.connect(DB_PATH)
                cur = conn.execute("SELECT DISTINCT telegram_id FROM links")
                rows = cur.fetchall()
                conn.close()

                for row in rows:
                    send_message(row[0], msg, auto_delete=False)

                if GROUP_CHAT_ID:
                    send_message(GROUP_CHAT_ID, msg, auto_delete=False)

            # Reset LWK war-end flag
            if w and w.get("state") == "preparation":
                notified_lwk_end = False

            # Ordinary war 3h reminder
            global notified_war
            w2 = get_war()
            if w2 and w2.get("state") == "inWar":
                end2 = parse_time(w2.get("endTime"))
                if end2:
                    now2 = datetime.now(timezone.utc)
                    diff_hours2 = (end2 - now2).total_seconds() / 3600

                    # notify once
                    if diff_hours2 <= 3 and diff_hours2 > 0 and not notified_war:
                        notified_war = True

                        # Build missed list
                        missed = []
                        for m in w2["clan"]["members"]:
                            if not m.get("attacks"):
                                missed.append(f"{m['name']} — {m['tag']}")

                        message = "⏳ *До конца обычной войны осталось 3 часа!*\n\n"
                        if missed:
                            message += "❗ Не били:\n" + "\n".join(missed)
                        else:
                            message += "🎉 Все уже атаковали!"

                        # Send to subscribers
                        subs = db_get_subs()
                        for uid, st in subs:
                            if st == "war_3h":
                                send_message(uid, message, auto_delete=False)

                        # Send to group chat if configured
                        if GROUP_CHAT_ID:
                            send_message(GROUP_CHAT_ID, message, auto_delete=False)

                    # reset for next war
                    if diff_hours2 > 3:
                        notified_war = False

            # Ordinary war ended notification (notify once)
            global notified_war_end
            if not 'notified_war_end' in globals():
                notified_war_end = False

            if w2 and w2.get("state") == "warEnded" and not notified_war_end:
                notified_war_end = True

                clan = w2.get("clan", {})
                opp = w2.get("opponent", {})

                clan_name = clan.get("name", "Наш клан")
                opp_name = opp.get("name", "Противник")

                clan_stars = clan.get("stars", 0)
                opp_stars = opp.get("stars", 0)

                clan_atk = clan.get("attacks", 0)
                opp_atk = opp.get("attacks", 0)

                result = "Ничья"
                if clan_stars > opp_stars:
                    result = "🏆 Победа!"
                elif clan_stars < opp_stars:
                    result = "❌ Поражение"

                missed = []
                for m in clan.get("members", []):
                    if not m.get("attacks"):
                        missed.append(f"{m['name']} — {m['tag']}")

                msg = (
                    "🔔 *Обычная война завершилась!*\n\n"
                    f"{clan_name} — {opp_name}\n"
                    f"⭐ {clan_stars} — {opp_stars}\n"
                    f"⚔ {clan_atk} атак — {opp_atk} атак\n\n"
                    f"🏁 Итог: *{result}*\n\n"
                )

                if missed:
                    msg += "❗ *Не атаковали:*\n" + "\n".join(missed)

                # Send to group
                if GROUP_CHAT_ID:
                    send_message(GROUP_CHAT_ID, msg, auto_delete=False)

                # Send to all subscribers
                subs = db_get_subs()
                for uid, st in subs:
                    if st in ("war_start", "war_3h"):
                        send_message(uid, msg, auto_delete=False)

            # Reset war-end notifier for next war
            if w2 and w2.get("state") == "preparation":
                notified_war_end = False

            # War start reminder (notify once)
            global notified_war_start
            if w2 and w2.get("state") == "preparation":
                start2 = parse_time(w2.get("startTime"))
                if start2:
                    now3 = datetime.now(timezone.utc)
                    diff_hours3 = (start2 - now3).total_seconds() / 3600
                    if diff_hours3 <= 0 and not notified_war_start:
                        notified_war_start = True
                        subs = db_get_subs()
                        for uid, st in subs:
                            if st == "war_start":
                                send_message(uid, "🔥 Война началась! Вперёд в бой!", auto_delete=False)
                        if GROUP_CHAT_ID:
                            send_message(GROUP_CHAT_ID, "🔥 Война началась! Вперёд в бой!", auto_delete=False)
            # Reset for next war
            if w2 and w2.get("state") == "warEnded":
                notified_war_start = False

        except Exception as e:
            print("Notifier error:", e)

        time.sleep(60)

# -------------------- MAIN --------------------

def main():
    print("Бот запущен!")
    db_init()
    threading.Thread(target=auto_cleanup_worker, daemon=True).start()
    threading.Thread(target=background_notifier, daemon=True).start()
    last_update = None

    while True:
        updates = get_updates(last_update + 1 if last_update else None)
        for upd in updates:
            last_update = upd["update_id"]
            handle_update(upd)
        time.sleep(1)


        

if __name__ == "__main__":
    main()
