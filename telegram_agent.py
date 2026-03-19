import os
import json
import sqlite3
import imaplib
import email
from email.header import decode_header
import anthropic
import resend
from groq import Groq
from datetime import datetime, timedelta
from tavily import TavilyClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import base64
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
tavily = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
resend.api_key = os.environ.get("RESEND_API_KEY")
TELEGRAM_KEY = os.environ.get("TELEGRAM_TOKEN")
ICLOUD_EMAIL = os.environ.get("ICLOUD_EMAIL")
ICLOUD_PASSWORD = os.environ.get("ICLOUD_PASSWORD")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
DB_PATH = "memory.db"

def get_calendar_service():
    from google.auth.transport.requests import Request
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    if isinstance(content, list):
        serializable = []
        for block in content:
            if hasattr(block, 'model_dump'):
                serializable.append(block.model_dump())
            elif hasattr(block, '__dict__'):
                serializable.append(block.__dict__)
            else:
                serializable.append(block)
        content = json.dumps(serializable)
    conn.execute("INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content))
    conn.commit()
    conn.close()

def get_history(user_id, limit=20):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)).fetchall()
    conn.close()
    messages = []
    for role, content in reversed(rows):
        try:
            content = json.loads(content)
        except:
            pass
        messages.append({"role": role, "content": content})
    return messages

def search_web(query):
    print(f"Ищу: {query}")
    result = tavily.search(query=query, max_results=5, search_depth="advanced")
    text = ""
    for r in result.get("results", []):
        text += f"- {r['title']}: {r['content']}\n"
    return text

def decode_subject(raw_subject):
    if not raw_subject:
        return "(без темы)"
    try:
        parts = decode_header(raw_subject)
        result = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                result += part.decode(enc or "utf-8", errors="ignore")
            else:
                result += str(part)
        return result
    except:
        return str(raw_subject)

def get_body(msg):
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    raw = part.get_payload(decode=True)
                    if raw and isinstance(raw, bytes):
                        return raw.decode(charset, errors="ignore")
            return ""
        else:
            charset = msg.get_content_charset() or "utf-8"
            raw = msg.get_payload(decode=True)
            if raw and isinstance(raw, bytes):
                return raw.decode(charset, errors="ignore")
            return ""
    except Exception as e:
        print(f"Ошибка get_body: {e}")
        return ""

def get_emails(count=5):
    try:
        mail = imaplib.IMAP4_SSL("imap.mail.me.com")
        mail.login(ICLOUD_EMAIL, ICLOUD_PASSWORD)
        _, folders = mail.list()
        all_emails = []
        for folder in folders:
            try:
                folder_name = folder.decode().split('"/"')[-1].strip().strip('"')
                status, _ = mail.select(folder_name, readonly=True)
                if status != "OK":
                    continue
                _, data = mail.search(None, "ALL")
                if not data[0]:
                    continue
                ids = data[0].split()
                for eid in ids[-2:]:
                    try:
                        _, msg_data = mail.fetch(eid, "(RFC822)")
                        raw_email = msg_data[0][1]
                        if not isinstance(raw_email, bytes):
                            continue
                        msg = email.message_from_bytes(raw_email)
                        subject = decode_subject(msg.get("Subject", ""))
                        from_ = msg.get("From", "")
                        date_ = msg.get("Date", "")
                        body = get_body(msg)
                        all_emails.append({"date": date_, "text": f"От: {from_}\nТема: {subject}\nДата: {date_}\n{body[:300]}"})
                    except:
                        continue
            except:
                continue
        mail.logout()
        if not all_emails:
            return "Письма не найдены"
        return "\n\n---\n\n".join([e["text"] for e in all_emails[-count:]])
    except Exception as e:
        print(f"Ошибка чтения почты: {e}")
        return f"Ошибка чтения почты: {e}"

def send_email(to, subject, body):
    try:
        params = {"from": "AI Agent <onboarding@resend.dev>", "to": [to], "subject": subject, "text": body}
        resend.Emails.send(params)
        return f"Письмо отправлено на {to}"
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return f"Ошибка отправки: {e}"

def get_calendar_events(days=7):
    try:
        service = get_calendar_service()
        now = datetime.utcnow().isoformat() + "Z"
        end = (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"
        events_result = service.events().list(
            calendarId="primary", timeMin=now, timeMax=end,
            maxResults=10, singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        if not events:
            return "Событий не найдено"
        result = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            result.append(f"- {e.get('summary', 'Без названия')} — {start}")
        return "\n".join(result)
    except Exception as e:
        print(f"Ошибка календаря: {e}")
        return f"Ошибка календаря: {e}"

def create_calendar_event(title, date, time=None, description=""):
    try:
        service = get_calendar_service()
        if time:
            start_dt = f"{date}T{time}:00"
            end_dt = (datetime.fromisoformat(start_dt) + timedelta(hours=1)).isoformat()
            event = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start_dt, "timeZone": "Asia/Dubai"},
                "end": {"dateTime": end_dt, "timeZone": "Asia/Dubai"},
            }
        else:
            event = {
                "summary": title,
                "description": description,
                "start": {"date": date},
                "end": {"date": date},
            }
        service.events().insert(calendarId="primary", body=event).execute()
        return f"Событие '{title}' создано на {date}"
    except Exception as e:
        print(f"Ошибка создания события: {e}")
        return f"Ошибка создания события: {e}"

def transcribe_voice(file_path):
    with open(file_path, "rb") as f:
        transcription = groq_client.audio.transcriptions.create(
            file=(os.path.basename(file_path), f),
            model="whisper-large-v3",
            language="ru"
        )
    return transcription.text

tools = [
    {"name": "search_web", "description": "Поиск актуальной информации в интернете", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_emails", "description": "Получить последние письма из iCloud почты пользователя", "input_schema": {"type": "object", "properties": {"count": {"type": "integer"}}}},
    {"name": "send_email", "description": "Отправить письмо пользователя", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "get_calendar_events", "description": "Получить предстоящие события из Google Calendar", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "create_calendar_event", "description": "Создать новое событие в Google Calendar", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"}, "time": {"type": "string", "description": "Время в формате HH:MM"}, "description": {"type": "string"}}, "required": ["title", "date"]}}
]

async def process_message(update: Update, user_id: int, user_name: str, user_text: str):
    today = datetime.now().strftime("%d.%m.%Y")
    save_message(user_id, "user", user_text)
    messages = get_history(user_id)
    while True:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            system=f"Ты полезный персональный ассистент пользователя {user_name}. Сегодня {today}. Отвечай на русском языке. Можешь искать информацию в интернете, читать и отправлять письма, управлять календарём пользователя.",
            tools=tools,
            messages=messages
        )
        if response.stop_reason == "end_turn":
            answer = response.content[0].text
            save_message(user_id, "assistant", answer)
            await update.message.reply_text(answer)
            break
        if response.stop_reason == "tool_use":
            save_message(user_id, "assistant", response.content)
            messages = get_history(user_id)
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if block.name == "search_web":
                        result = search_web(block.input["query"])
                    elif block.name == "get_emails":
                        result = get_emails(block.input.get("count", 5))
                    elif block.name == "send_email":
                        result = send_email(block.input["to"], block.input["subject"], block.input["body"])
                    elif block.name == "get_calendar_events":
                        result = get_calendar_events(block.input.get("days", 7))
                    elif block.name == "create_calendar_event":
                        result = create_calendar_event(block.input["title"], block.input["date"], block.input.get("time"), block.input.get("description", ""))
                    else:
                        result = "Инструмент не найден"
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            save_message(user_id, "user", tool_results)
            messages = get_history(user_id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    user_text = update.message.text
    await update.message.chat.send_action("typing")
    await process_message(update, user_id, user_name, user_text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = f"/tmp/photo_{user_id}.jpg"
    await file.download_to_drive(file_path)
    with open(file_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")
    os.remove(file_path)
    caption = update.message.caption or "Что на этом изображении? Опиши подробно."
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}}, {"type": "text", "text": caption}]}]
    )
    answer = response.content[0].text
    save_message(user_id, "user", f"[Фото] {caption}")
    save_message(user_id, "assistant", answer)
    await update.message.reply_text(answer)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    file_path = f"/tmp/doc_{user_id}_{doc.file_name}"
    await file.download_to_drive(file_path)
    caption = update.message.caption or "Проанализируй этот файл."
    if doc.mime_type == "application/pdf":
        with open(file_path, "rb") as f:
            pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}}, {"type": "text", "text": caption}]}]
        )
    elif doc.mime_type and doc.mime_type.startswith("image/"):
        with open(file_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": image_data}}, {"type": "text", "text": caption}]}]
        )
    else:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()[:5000]
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2048,
                messages=[{"role": "user", "content": f"{caption}\n\nСодержимое файла:\n{text}"}]
            )
        except:
            os.remove(file_path)
            await update.message.reply_text("Не могу прочитать этот тип файла.")
            return
    os.remove(file_path)
    answer = response.content[0].text
    save_message(user_id, "user", f"[Файл: {doc.file_name}] {caption}")
    save_message(user_id, "assistant", answer)
    await update.message.reply_text(answer)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    await update.message.chat.send_action("typing")
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    file_path = f"/tmp/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)
    user_text = transcribe_voice(file_path)
    os.remove(file_path)
    print(f"Распознано: {user_text}")
    await process_message(update, user_id, user_name, user_text)

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(f"Привет, {name}! Я твой персональный ассистент. Могу искать информацию, читать и отправлять письма, управлять календарём, анализировать фото и файлы, принимать голосовые сообщения!")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_KEY).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()