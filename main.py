import asyncio
import json
import websockets
from aiogram import Bot
from aiogram.enums import ParseMode

# Конфигурация
TELEGRAM_BOT_TOKEN = ""  # Замените на ваш токен бота Telegram
TELEGRAM_CHAT_ID = ""  # Замените на ID группы Telegram
MAX_TOKEN = ""  # Замените на ваш токен Max

bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def send_to_telegram(text):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.HTML)


async def get_user_name(websocket, sender_id):
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": 2,
        "opcode": 32,
        "payload": {
            "contactIds": [sender_id]
        }
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
                    "osVersion": "ResendLinux",
                    "deviceName": "Firefox",
                    "headerUserAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
                    "appVersion": "25.7.11",
                    "screen": "827x1323 1.9x",
                    "timezone": "Europe/Moscow"
                },
                "deviceId": "d4a88e2a-dc04-48ca-b918-6fd91ddf392d"
            }
        }
        await websocket.send(json.dumps(first_message))

        response = await websocket.recv()
        print(f"Получен ответ на первое сообщение: {response}")

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
                        if chat.get("type") == "CHAT":  # Только групповые чаты
                            groups[str(chat["id"])] = chat.get("title", str(chat["id"]))
                    print("Группы обновлены:", groups)

                elif data["opcode"] == 64:
                    sender = str(data["payload"]["message"]["sender"])
                    text = data["payload"]["message"].get("text", "")
                    sender_name = await get_user_name(websocket, sender)  # Запрос имени
                    await send_to_telegram(
                        f"({sender})\nБыло получено новое личное сообщение от <b>{sender_name}</b>, его текст:\n\n<code>{text}</code>"
                    )

                elif data["opcode"] == 128:
                    chat_id = str(data["payload"]["chatId"])
                    sender = str(data["payload"]["message"]["sender"])
                    text = data["payload"]["message"].get("text", "")
                    chat_name = groups.get(chat_id, chat_id)  # Название группы или ID
                    sender_name = await get_user_name(websocket, sender)  # Запрос имени
                    await send_to_telegram(
                        f"({chat_id}, {sender})\nБыло получено новое сообщение из группы <b>{chat_name}</b> от <b>{sender_name}</b>, его текст:\n\n<code>{text}</code>"
                    )

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