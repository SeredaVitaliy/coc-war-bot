import os
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# -------------------- ЗАГРУЗКА .env --------------------

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COC_API_TOKEN = os.getenv("COC_API_TOKEN")
CLAN_TAG = os.getenv("CLAN_TAG")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("ERROR: TELEGRAM_BOT_TOKEN не найден в .env")

if not COC_API_TOKEN:
    print("⚠️ COC_API_TOKEN не найден — команды войны работать не будут.")

if not CLAN_TAG:
    print("⚠️ CLAN_TAG не найден — команды войны работать не будут.")

BASE_TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
COC_URL = f"https://api.clashofclans.com/v1/clans/{CLAN_TAG}/currentwar"
PLAYER_URL = "https://api.clashofclans.com/v1/players/"

COC_HEADERS = {"Authorization": f"Bearer {COC_API_TOKEN}"}

# -------------------- ЛОКАЛЬНАЯ БД (в одном файле) --------------------

DB_PATH = "links.db"

def db_init():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            telegram_id TEXT PRIMARY KEY,
            coc_tag TEXT NOT NULL
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
        ON CONFLICT(telegram_id)
        DO UPDATE SET coc_tag = excluded.coc_tag
    """, (str(telegram_id), coc_tag))
    conn.commit()
    conn.close()

def db_get_link(telegram_id):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT coc_tag FROM links WHERE telegram_id=?", (str(telegram_id),))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

# -------------------- УТИЛИТЫ --------------------

def send_message(chat_id, text):
    try:
        requests.post(
            BASE_TELEGRAM_URL + "/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except:
        pass

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
    try:
        r = requests.get(COC_URL, headers=COC_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_player(tag):
    url = PLAYER_URL + requests.utils.quote(tag)
    try:
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

# -------------------- КОМАНДЫ --------------------

def cmd_start(chat_id):
    send_message(chat_id,
        "👋 Привет! Я бот клана Clash of Clans.\n\n"
        "Команды:\n"
        "/warstatus — статус войны\n"
        "/warstart — время до начала\n"
        "/warend — время до конца\n"
        "/warleft — сколько атак осталось\n"
        "/missed — кто не бил\n"
        "/link #TAG — привязать Clash профиль\n"
        "/mytag — показать привязанный тег\n"
        "/myattacks — показать твои атаки\n"
    )

def cmd_warstatus(chat_id):
    w = get_war()
    if not w:
        send_message(chat_id, "Не удалось получить данные о войне.")
        return
    state = w.get("state", "?")
    msg = f"📌 Статус войны: *{state}*\n\n"
    if "startTime" in w:
        start = parse_time(w["startTime"])
        msg += f"До начала: {format_diff(start)}\n"
    if "endTime" in w:
        end = parse_time(w["endTime"])
        msg += f"До конца: {format_diff(end)}\n"
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

def cmd_missed(chat_id):
    w = get_war()
    if not w or w["state"] != "inWar":
        send_message(chat_id, "Война не идёт.")
        return
    msg = "❗ Не сделали ни одной атаки:\n\n"
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
    tag = parts[1].upper()
    if not tag.startswith("#"):
        send_message(chat_id, "Тег должен начинаться с #")
        return
    db_save_link(user_id, tag)
    send_message(chat_id, f"🔗 Тег {tag} привязан к вашему профилю.")

def cmd_mytag(chat_id, user_id):
    tag = db_get_link(user_id)
    if not tag:
        send_message(chat_id, "У вас нет привязанного профиля. Используйте /link #TAG")
    else:
        send_message(chat_id, f"Ваш тег: *{tag}*")

def cmd_myattacks(chat_id, user_id):
    tag = db_get_link(user_id)
    if not tag:
        send_message(chat_id, "Сначала привяжите тег через /link #TAG")
        return
    w = get_war()
    if not w or w["state"] != "inWar":
        send_message(chat_id, "Нет текущей войны.")
        return
    members = w["clan"]["members"]
    me = next((m for m in members if m["tag"] == tag), None)
    if not me:
        send_message(chat_id, "Вы не участвуете в этой войне.")
        return
    if not me.get("attacks"):
        send_message(chat_id, "Вы ещё не атаковали.")
        return
    msg = "Ваши атаки:\n\n"
    for atk in me["attacks"]:
        msg += f"⭐ {atk['stars']} — {atk['destructionPercentage']}%\n"
    send_message(chat_id, msg)

# -------------------- ОБРАБОТКА --------------------

def handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    user_id = msg["from"]["id"]

    print(f"[UPDATE] chat={chat_id} text={text}")

    if text == "/start":
        cmd_start(chat_id)
    elif text == "/warstatus":
        cmd_warstatus(chat_id)
    elif text == "/warstart":
        cmd_warstart(chat_id)
    elif text == "/warend":
        cmd_warend(chat_id)
    elif text == "/warleft":
        cmd_warleft(chat_id)
    elif text == "/missed":
        cmd_missed(chat_id)
    elif text.startswith("/link"):
        cmd_link(chat_id, user_id, text)
    elif text == "/mytag":
        cmd_mytag(chat_id, user_id)
    elif text == "/myattacks":
        cmd_myattacks(chat_id, user_id)
    else:
        send_message(chat_id, "Не знаю такую команду. Используйте /start")

# -------------------- MAIN --------------------

def main():
    print("Бот запущен!")
    db_init()
    last_update = None

    while True:
        updates = get_updates(last_update + 1 if last_update else None)
        for upd in updates:
            last_update = upd["update_id"]
            handle_update(upd)
        time.sleep(1)

if __name__ == "__main__":
    main()