import time
import requests
import os
from datetime import datetime, timezone
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COC_API_TOKEN = os.getenv("COC_API_TOKEN")
CLAN_TAG = os.getenv("CLAN_TAG")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ================= TELEGRAM =================

def get_updates(offset=None, timeout=30):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    resp = requests.get(f"{BASE_URL}/getUpdates", params=params)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        print("Ошибка getUpdates:", data)
        return []

    return data["result"]


def send_message(chat_id, text):
    resp = requests.post(
        f"{BASE_URL}/sendMessage",
        data={"chat_id": chat_id, "text": text},
    )
    if not resp.ok:
        print("Ошибка sendMessage:", resp.text)


# ================= CLASH OF CLANS API =================

def get_clan_war():
    try:
        encoded_tag = quote(CLAN_TAG)
        url = f"https://api.clashofclans.com/v1/clans/{encoded_tag}/currentwar"

        headers = {
            "Authorization": f"Bearer {COC_API_TOKEN}",
            "Accept": "application/json",
        }

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print("Ошибка CoC API:", resp.status_code, resp.text)
            return None

        return resp.json()
    except Exception as e:
        print("Ошибка обращения к CoC API:", e)
        return None


# ================= TIME PARSER (ISO ONLY) =================

def parse_time(time_str: str):
    """
    Поддерживает один формат:
    20251121T103841.000Z
    """
    try:
        # убираем миллисекунды: "20251121T103841.000Z" → "20251121T103841Z"
        base = time_str.replace(".000", "")
        dt = datetime.strptime(base, "%Y%m%dT%H%M%SZ")
        return dt.replace(tzinfo=timezone.utc)
    except Exception as e:
        print("Ошибка парсинга ISO:", time_str, e)
        return None


def format_diff(target_dt: datetime):
    if not target_dt:
        return None

    now = datetime.now(timezone.utc)
    diff = (target_dt - now).total_seconds()

    if diff <= 0:
        return None

    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)

    return f"{hours}ч {minutes}м"


# ================= COMMAND HANDLING =================

def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if text == "/start":
        send_message(chat_id,
                     "Бот клана Clash of Clans:\n"
                     "/warstart — время до начала войны\n"
                     "/warend — время до конца войны")
        return

    # ---- /warstart ----
    if text == "/warstart":
        war = get_clan_war()
        if not war:
            send_message(chat_id, "Не удалось получить данные о войне.")
            return

        if war.get("state") != "preparation":
            send_message(chat_id, f"Сейчас не этап подготовки. Статус: {war.get('state')}")
            return

        start_dt = parse_time(war.get("startTime"))
        diff = format_diff(start_dt)

        if not diff:
            send_message(chat_id, "Война уже началась или время неизвестно.")
            return

        send_message(chat_id, f"До начала войны: {diff}")
        return

    # ---- /warend ----
    if text == "/warend":
        war = get_clan_war()
        if not war:
            send_message(chat_id, "Не удалось получить данные о войне.")
            return

        if war.get("state") != "inWar":
            send_message(chat_id, f"Война сейчас не идёт. Статус: {war.get('state')}")
            return

        end_dt = parse_time(war.get("endTime"))
        diff = format_diff(end_dt)

        if not diff:
            send_message(chat_id, "Война уже закончилась или время неизвестно.")
            return

        send_message(chat_id, f"До окончания войны: {diff}")
        return

    # Остальные команды
    send_message(
        chat_id,
        "Неизвестная команда. Используй:\n/warstart — до начала\n/warend — до конца"
    )


# ================= MAIN =================

def main():
    print("Бот запущен.")

    last_update_id = None

    while True:
        try:
            updates = get_updates(offset=(last_update_id + 1) if last_update_id else None)

            for update in updates:
                last_update_id = update["update_id"]
                handle_update(update)

        except KeyboardInterrupt:
            print("\nОстановка бота.")
            break

        except Exception as e:
            print("Ошибка в основном цикле:", e)

        time.sleep(1)


if __name__ == "__main__":
    main()