import os
import json
import sqlite3
import anthropic
from datetime import datetime
from tavily import TavilyClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
tavily = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))
TELEGRAM_KEY = os.environ.get("TELEGRAM_TOKEN")
DB_PATH = "memory.db"

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
    conn.execute("INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, json.dumps(content) if isinstance(content, list) else content))
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

tools = [{"name": "search_web", "description": "Глубокий поиск актуальной информации в интернете", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    user_text = update.message.text
    today = datetime.now().strftime("%d.%m.%Y")
    await update.message.chat.send_action("typing")
    save_message(user_id, "user", user_text)
    messages = get_history(user_id)
    while True:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            system=f"Ты полезный персональный ассистент пользователя {user_name}. Сегодня {today}. Отвечай на русском языке. Используй поиск для актуальной информации. При поиске событий и концертов ищи только будущие события после {today}. Давай подробные и конкретные ответы.",
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
                    result = search_web(block.input["query"])
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            save_message(user_id, "user", tool_results)
            messages = get_history(user_id)

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(f"Привет, {name}! Я твой персональный ассистент. Знаю сегодняшнюю дату и найду только актуальные события. Чем могу помочь?")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_KEY).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
