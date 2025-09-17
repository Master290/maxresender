import asyncio
import json
import websockets
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto
)

# ================== Конфигурация ==================
TELEGRAM_BOT_TOKEN = ""  # <-- замени на токен бота Telegram
TELEGRAM_CHAT_ID = ""    # <-- ID группы Telegram
MAX_TOKEN = ""            # <-- токен MAX (https://github.com/Master290/maxresender/blob/main/maxtoken.md)
# ==================================================

bot = Bot(token=TELEGRAM_BOT_TOKEN)


def build_keyboard(sender_name=None, chat_name=None):
    if sender_name:
        btn_text = f"👤 {sender_name}"
        if chat_name:
            btn_text += f" | 💬 {chat_name}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=btn_text, callback_data="noop")]
            ]
        )
    return None


async def send_to_telegram(text, sender_name=None, chat_name=None):
    kb = build_keyboard(sender_name, chat_name)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


async def send_attachments(attaches, sender_name=None, chat_name=None):
    if not attaches:
        return

    photos = [a for a in attaches if a.get("_type") == "PHOTO" and a.get("baseUrl")]
    kb = build_keyboard(sender_name, chat_name)

    # альбом фото
    if len(photos) > 1:
        media = [InputMediaPhoto(media=p["baseUrl"]) for p in photos]
        await bot.send_media_group(TELEGRAM_CHAT_ID, media=media)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="📷 Альбом",
            reply_markup=kb
        )
    elif len(photos) == 1:
        await bot.send_photo(
            TELEGRAM_CHAT_ID,
            photo=photos[0]["baseUrl"],
            caption="📷 Фото",
            reply_markup=kb
        )


async def get_user_name(websocket, sender_id):
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": 2,
        "opcode": 32,
        "payload": {"contactIds": [sender_id]}
    }
    await websocket.send(json.dumps(request))
    response = await websocket.recv()
    data = json.loads(response)

    if data.get("opcode") == 32 and data.get("payload", {}).get("contacts"):
        for contact in data["payload"]["contacts"]:
            if str(contact.get("id")) == str(sender_id):
                return contact.get("names", [{}])[0].get("name", sender_id)
    return sender_id


async def connect_to_max(maxtoken):
    uri = "wss://ws-api.oneme.ru/websocket"
    async with websockets.connect(uri) as websocket:
        # первое сообщение
        first_message = {
            "ver": 11,
            "cmd": 0,
            "seq": 2,
            "opcode": 6,
            "payload": {
                "userAgent": {
                    "deviceType": "WEB",
                    "locale": "ru",
                    "deviceLocale": "en",
                    "osVersion": "Linux",
                    "deviceName": "Firefox",
                    "headerUserAgent": "Mozilla/5.0",
                    "appVersion": "25.7.11",
                    "screen": "827x1323 1.9x",
                    "timezone": "Europe/Moscow"
                },
                "deviceId": "device id"
            }
        }
        await websocket.send(json.dumps(first_message))
        await websocket.recv()

        # второе сообщение
        second_message = {
            "ver": 11,
            "cmd": 0,
            "seq": 3,
            "opcode": 19,
            "payload": {
                "interactive": False,
                "token": maxtoken,
                "chatsSync": 0,
                "contactsSync": 0,
                "presenceSync": 0,
                "draftsSync": 0,
                "chatsCount": 40
            }
        }
        await websocket.send(json.dumps(second_message))

        groups = {}

        while True:
            try:
                message = await websocket.recv()
                data = json.loads(message)

                if data["opcode"] == 19:
                    for chat in data["payload"].get("chats", []):
                        if chat.get("type") == "CHAT":
                            groups[str(chat["id"])] = chat.get("title", str(chat["id"]))
                    print("Группы обновлены:", groups)

                elif data["opcode"] == 64:  # личные
                    sender = str(data["payload"]["message"]["sender"])
                    text = data["payload"]["message"].get("text", "")
                    attaches = data["payload"]["message"].get("attaches", [])
                    sender_name = await get_user_name(websocket, sender)

                    await send_to_telegram(
                        f"Новое личное сообщение:\n\n<code>{text}</code>",
                        sender_name=sender_name
                    )
                    await send_attachments(attaches, sender_name=sender_name)

                elif data["opcode"] == 128:  # групповые
                    chat_id = str(data["payload"]["chatId"])
                    sender = str(data["payload"]["message"]["sender"])
                    text = data["payload"]["message"].get("text", "")
                    attaches = data["payload"]["message"].get("attaches", [])
                    chat_name = groups.get(chat_id, chat_id)
                    sender_name = await get_user_name(websocket, sender)

                    await send_to_telegram(
                        f"{text}",
                        sender_name=sender_name,
                        chat_name=chat_name
                    )
                    await send_attachments(attaches, sender_name=sender_name, chat_name=chat_name)

            except Exception as e:
                print(f"Ошибка при обработке сообщения: {e}")


async def main():
    try:
        await connect_to_max(MAX_TOKEN)
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

