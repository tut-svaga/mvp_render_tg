import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


def send_prediction() -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID_SER")

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID_SER is not set.")

    message_path = Path("output_stage_d/stage_d_telegram_message.html")
    if not message_path.is_file():
        raise FileNotFoundError(f"Telegram message file not found: {message_path}")
    message = message_path.read_text(encoding="utf-8")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API returned an unsuccessful response: {payload}")
    return payload


if __name__ == "__main__":
    result = send_prediction()
    print("Telegram message sent. Message ID:", result.get("result", {}).get("message_id"))
