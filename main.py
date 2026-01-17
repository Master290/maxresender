import asyncio
import json
import os

import websockets
from websockets.exceptions import ConnectionClosed
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, BufferedInputFile
)
import aiohttp
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID")
ENABLE_SUPERCHATS = os.getenv("ENABLE_SUPERCHATS", "True").lower() == "true"

if TELEGRAM_THREAD_ID and TELEGRAM_THREAD_ID.strip() and ENABLE_SUPERCHATS:
    try:
        TELEGRAM_THREAD_ID = int(TELEGRAM_THREAD_ID.strip())
    except ValueError:
        print(f"Некорректный TELEGRAM_THREAD_ID: {TELEGRAM_THREAD_ID}. Используется None.")
        TELEGRAM_THREAD_ID = None
else:
    TELEGRAM_THREAD_ID = None
MAX_TOKEN = os.getenv("MAX_TOKEN")
MAX_WS_URI = os.getenv("MAX_WS_URI", "wss://ws-api.oneme.ru/websocket")
MAX_WS_ORIGIN = os.getenv("MAX_WS_ORIGIN", "https://web.max.ru")
raw_allowed_ids = os.getenv("MAX_ALLOWED_CHAT_IDS", "").split(",")
MAX_ALLOWED_CHAT_IDS = {cid.strip() for cid in raw_allowed_ids if cid.strip()}
RECONNECT_DELAY = 5

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN в .env")
if not TELEGRAM_CHAT_ID:
    raise RuntimeError("Укажите TELEGRAM_CHAT_ID в .env")
if not MAX_TOKEN:
    raise RuntimeError("Укажите MAX_TOKEN в .env")

if not MAX_ALLOWED_CHAT_IDS:
    print("Укажите MAX_ALLOWED_CHAT_IDS в .env (через запятую), чтобы пересылать сообщения только из нужных групп.")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
_seq = 100
_dispatcher = {}  # {seq: asyncio.Future}
_session = None
_name_cache = {}  # {user_id: name}

def next_seq():
    global _seq
    _seq += 1
    return _seq

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session



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


async def get_file_url(websocket, file_id, chat_id, message_id):
    seq = next_seq()
    s_seq = str(seq)
    future = asyncio.get_running_loop().create_future()
    _dispatcher[s_seq] = future
    
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": seq,
        "opcode": 88,
        "payload": {
            "fileId": file_id,
            "chatId": int(chat_id),
            "messageId": str(message_id)
        }
    }
    try:
        await websocket.send(json.dumps(request))
        data = await asyncio.wait_for(future, timeout=5.0)
        return data.get("payload", {}).get("url")
    except Exception as e:
        print(f"Ошибка при ожидании ссылки на файл: {e}")
        return None
    finally:
        _dispatcher.pop(s_seq, None)


async def get_video_url(websocket, video_id, chat_id, message_id):
    seq = next_seq()
    s_seq = str(seq)
    future = asyncio.get_running_loop().create_future()
    _dispatcher[s_seq] = future
    
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": seq,
        "opcode": 83,
        "payload": {
            "videoId": video_id,
            "chatId": int(chat_id),
            "messageId": str(message_id)
        }
    }
    try:
        await websocket.send(json.dumps(request))
        data = await asyncio.wait_for(future, timeout=5.0)
        payload = data.get("payload", {})
        # пробуем разные качества хз мб подойдет
        for quality in ["MP4_720", "MP4_480", "MP4_360", "MP4_1080"]:
            if quality in payload:
                return payload[quality]
        # резервный поиск любой ссылки
        for val in payload.values():
            if isinstance(val, str) and val.startswith("http"):
                return val
        return None
    except Exception as e:
        print(f"Ошибка при ожидании ссылки на видео: {e}")
        return None
    finally:
        _dispatcher.pop(s_seq, None)



async def send_to_telegram(text, sender_name=None, chat_name=None, escape=True):
    clean_text = (text or "").strip()
    
    if not clean_text:
        final_text = "<i>(пустое сообщение)</i>"
    else:
        if escape:
            final_text = clean_text.replace("<", "&lt;").replace(">", "&gt;")
        else:
            final_text = clean_text

    kb = build_keyboard(sender_name, chat_name)
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            message_thread_id=TELEGRAM_THREAD_ID
        )
    except Exception as e:
        print(f"Ошибка при отправке в Telegram: {e}")


async def send_attachments(websocket, attaches, chat_id, message_id, sender_name=None, chat_name=None):
    if not attaches:
        return

    kb = build_keyboard(sender_name, chat_name)
    
    album_candidates = [a for a in attaches if a.get("_type") in ["PHOTO", "VIDEO"]]
    remaining_attaches = [a for a in attaches if a not in album_candidates]

    if album_candidates:
        if len(album_candidates) > 1:
            media_list = []
            for a in album_candidates[:10]:
                atype = a.get("_type")
                file_id = a.get("fileId") or a.get("videoId")
                
                target_url = a.get("baseUrl") or a.get("url")
                if not target_url and atype == "VIDEO" and file_id:
                    target_url = await get_video_url(websocket, file_id, chat_id, message_id)
                elif not target_url and file_id:
                    target_url = await get_file_url(websocket, file_id, chat_id, message_id)
                
                if target_url:
                    try:
                        session = await get_session()
                        async with session.get(target_url) as resp:
                            if resp.status == 200:
                                content = await resp.read()
                                if len(content) <= 50 * 1024 * 1024:
                                    ext = ".jpg" if atype == "PHOTO" else ".mp4"
                                    fname = a.get("name") or (f"media{ext}")
                                    input_file = BufferedInputFile(content, filename=fname)
                                    
                                    if atype == "PHOTO":
                                        media_list.append(InputMediaPhoto(media=input_file))
                                    else:
                                        media_list.append(InputMediaVideo(media=input_file))
                    except Exception as e:
                        print(f"Ошибка при скачивании медиа для альбома: {e}")

            if media_list:
                try:
                    await bot.send_media_group(TELEGRAM_CHAT_ID, media=media_list, message_thread_id=TELEGRAM_THREAD_ID)
                    await bot.send_message(
                        TELEGRAM_CHAT_ID, 
                        text="Фото/Видео", 
                        reply_markup=kb, 
                        message_thread_id=TELEGRAM_THREAD_ID
                    )
                except Exception as e:
                    print(f"Ошибка при отправке альбома: {e}")
        else:
            remaining_attaches.insert(0, album_candidates[0])

    for a in remaining_attaches:
        print(f"DEBUG: Аттач пришел: {json.dumps(a)}")
        
        atype = a.get("_type")
        file_id = a.get("fileId") or a.get("videoId") or a.get("audioId")

        file_name = a.get("name")
        if not file_name:
            if atype == "VIDEO": file_name = "Видео"
            elif atype == "PHOTO": file_name = "Фото"
            elif atype == "AUDIO": file_name = "Аудио"
            else: file_name = "Файл"
            
        base_url = a.get("baseUrl")
        direct_url = a.get("url")
        
        if base_url:
            target_url = base_url
        elif direct_url:
            target_url = direct_url
        elif atype in ["VIDEO", "VIDEO_MSG"] and file_id:
            target_url = await get_video_url(websocket, file_id, chat_id, message_id)
        elif file_id:
            target_url = await get_file_url(websocket, file_id, chat_id, message_id)
        else:
            print(f"DEBUG: Не найден URL или ID для вложения: {atype}")
            continue

        if target_url:
            try:
                session = await get_session()
                async with session.get(target_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        file_size = len(content)
                        
                        if file_size > 50 * 1024 * 1024: # лимит тг - 50МБ
                            await send_to_telegram(
                                f"⚠️ Файл <b>{file_name}</b> слишком велик для пересылки (>{file_size//1024//1024}MB).",
                                sender_name=sender_name,
                                chat_name=chat_name
                            )
                            continue

                        input_file = BufferedInputFile(content, filename=file_name)
                        
                        if atype == "PHOTO":
                            if not file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                                input_file.filename = "photo.jpg"
                            await bot.send_photo(TELEGRAM_CHAT_ID, photo=input_file, caption="📷 Фото", reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                        elif atype == "VOICE":
                            await bot.send_voice(TELEGRAM_CHAT_ID, voice=input_file, reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                        elif atype == "AUDIO":
                            if a.get("wave"):
                                await bot.send_voice(TELEGRAM_CHAT_ID, voice=input_file, reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                            else:
                                await bot.send_audio(TELEGRAM_CHAT_ID, audio=input_file, caption=f"🎵 {file_name}", reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                        elif atype == "VIDEO_MSG" or (atype == "VIDEO" and a.get("videoType") == 1 and a.get("width") == a.get("height")):
                            try:
                                if not input_file.filename.lower().endswith('.mp4'):
                                    input_file.filename = "video_note.mp4"
                                await bot.send_video_note(TELEGRAM_CHAT_ID, video_note=input_file, reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                            except Exception as ve:
                                print(f"Не удалось отправить как кружок, пробуем как видео: {ve}")
                                input_file = BufferedInputFile(content, filename="video.mp4")
                                await bot.send_video(TELEGRAM_CHAT_ID, video=input_file, caption="📹 Кружок", reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                        elif atype == "VIDEO":
                            await bot.send_video(TELEGRAM_CHAT_ID, video=input_file, caption=f"📹 {file_name}", reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                        else:
                            await bot.send_document(TELEGRAM_CHAT_ID, document=input_file, caption=f"📎 {file_name}", reply_markup=kb, message_thread_id=TELEGRAM_THREAD_ID)
                    else:
                        print(f"Не удалось скачать вложение {file_name}: статус {resp.status}")
            except Exception as e:
                print(f"Ошибка при обработке вложения {file_name} ({atype}): {e}")


async def get_user_name(websocket, sender_id):
    sender_id = str(sender_id)
    if sender_id in _name_cache:
        return _name_cache[sender_id]

    seq = next_seq()
    s_seq = str(seq)
    future = asyncio.get_running_loop().create_future()
    _dispatcher[s_seq] = future
    
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": seq,
        "opcode": 32,
        "payload": {"contactIds": [int(sender_id)]}
    }
    try:
        await websocket.send(json.dumps(request))
        data = await asyncio.wait_for(future, timeout=5.0)
        if data.get("opcode") == 32 and data.get("payload", {}).get("contacts"):
            for contact in data["payload"]["contacts"]:
                if str(contact.get("id")) == sender_id:
                    name = contact.get("names", [{}])[0].get("name", sender_id)
                    _name_cache[sender_id] = name
                    return name
    except Exception as e:
        print(f"Ошибка при запросе имени ({sender_id}): {e}")
    finally:
        _dispatcher.pop(s_seq, None)
    return sender_id


async def handle_max_message(websocket, data, groups):
    try:
        opcode = data.get("opcode")
        
        if opcode == 64:  # личные
            sender = str(data["payload"]["message"]["sender"])
            text = data["payload"]["message"].get("text", "")
            if text:
                text = text.replace("<", "&lt;").replace(">", "&gt;")
            
            attaches = data["payload"]["message"].get("attaches", [])
            if attaches is None: attaches = []

            link = data["payload"]["message"].get("link")
            if link and link.get("type") == "FORWARD" and link.get("message"):
                fwd_msg = link["message"]
                fwd_sender_id = str(fwd_msg.get("sender"))
                fwd_sender_name = await get_user_name(websocket, fwd_sender_id)
                fwd_text = fwd_msg.get("text", "")
                
                if fwd_text:
                    fwd_text = fwd_text.replace("<", "&lt;").replace(">", "&gt;")
                
                text = (text or "") + f"\n\n↩️ <b>Переслано от {fwd_sender_name}:</b>\n{fwd_text}"
                
                if fwd_msg.get("attaches"):
                    attaches.extend(fwd_msg["attaches"])
            
            sender_name = await get_user_name(websocket, sender)
            if text or not attaches:
                await send_to_telegram(
                    text,
                    sender_name=sender_name,
                    escape=False
                )
            await send_attachments(
                websocket, attaches, 
                chat_id=sender, message_id=data["payload"]["message"]["id"],
                sender_name=sender_name
            )

        elif opcode == 128:  # групповые
            chat_id = str(data["payload"]["chatId"])
            message_id = data["payload"]["message"]["id"]
            if MAX_ALLOWED_CHAT_IDS and chat_id not in MAX_ALLOWED_CHAT_IDS:
                return

            sender = str(data["payload"]["message"]["sender"])
            text = data["payload"]["message"].get("text", "")
            if text:
                text = text.replace("<", "&lt;").replace(">", "&gt;")
                
            attaches = data["payload"]["message"].get("attaches", [])
            if attaches is None: attaches = []

            link = data["payload"]["message"].get("link")
            if link and link.get("type") == "FORWARD" and link.get("message"):
                fwd_msg = link["message"]
                fwd_sender_id = str(fwd_msg.get("sender"))
                fwd_sender_name = await get_user_name(websocket, fwd_sender_id)
                fwd_text = fwd_msg.get("text", "")
                
                if fwd_text:
                    fwd_text = fwd_text.replace("<", "&lt;").replace(">", "&gt;")
                
                text = (text or "") + f"\n\n↩️ <b>Переслано от {fwd_sender_name}:</b>\n{fwd_text}"
                
                if fwd_msg.get("attaches"):
                    attaches.extend(fwd_msg["attaches"])
            
            chat_name = groups.get(chat_id, chat_id)
            sender_name = await get_user_name(websocket, sender)

            if text or not attaches:
                await send_to_telegram(
                    text,
                    sender_name=sender_name,
                    chat_name=chat_name,
                    escape=False
                )
            await send_attachments(
                websocket, attaches, 
                chat_id=chat_id, message_id=message_id,
                sender_name=sender_name, chat_name=chat_name
            )
    except Exception as e:
        print(f"Критическая ошибка при обработке сообщения: {e}")
        import traceback
        traceback.print_exc()


async def connect_to_max(maxtoken):
    while True:
        try:
            async with websockets.connect(
                MAX_WS_URI,
                origin=MAX_WS_ORIGIN,
                additional_headers={"User-Agent": "Mozilla/5.0"}
            ) as websocket: # тут подключение вебсокета вообще рофл ну и пусть будет как будто можно и получше сделать вообще хз честно говоря
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
                        
                        msg_seq = data.get("seq")
                        if msg_seq is not None:
                            s_seq = str(msg_seq)
                            if s_seq in _dispatcher:
                                _dispatcher[s_seq].set_result(data)
                                continue

                        # основная обработка по opcode
                        opcode = data.get("opcode")
                        if opcode == 19:
                            for chat in data["payload"].get("chats", []):
                                if chat.get("type") == "CHAT":
                                    groups[str(chat["id"])] = chat.get("title", str(chat["id"]))
                            print("Группы обновлены:", groups)
                        
                        elif opcode in [64, 128]:
                            asyncio.create_task(handle_max_message(websocket, data, groups))

                    except ConnectionClosed as e:
                        print(f"Соединение оборвано: {e}")
                        raise
                    except Exception as e:
                        print(f"Ошибка при обработке сообщения: {e}")
        except ConnectionClosed as e:
            print(f"Оборвано соединение: {e}. Пробуем еще раз через {RECONNECT_DELAY} секунд.")
        except Exception as e:
            print(f"Ошибка соединения: {e}. Пробуем еще раз через {RECONNECT_DELAY} секунд.")

        await asyncio.sleep(RECONNECT_DELAY)


async def main():
    try:
        await connect_to_max(MAX_TOKEN)
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        await bot.session.close()
        if _session and not _session.closed:
            await _session.close()


if __name__ == "__main__":
    asyncio.run(main())

