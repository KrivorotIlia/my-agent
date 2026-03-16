import os
import anthropic
from ddgs import DDGS
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

load_dotenv()

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_KEY = os.environ.get("TELEGRAM_TOKEN")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
conversations = {}

def search_web(query):
    print(f"Ищу: {query}")
    results = DDGS().text(query, max_results=3)
    text = ""
    for r in results:
        text += f"- {r['title']}: {r['body']}\n"
    return text

tools = [{"name": "search_web", "description": "Поиск в интернете", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    await update.message.chat.send_action("typing")
    if user_id not in conversations:
        conversations[user_id] = []
    conversations[user_id].append({"role": "user", "content": user_text})
    while True:
        response = client.messages.create(model="claude-opus-4-5", max_tokens=1024, system="Ты полезный ассистент. Отвечай на русском языке.", tools=tools, messages=conversations[user_id])
        if response.stop_reason == "end_turn":
            answer = response.content[0].text
            conversations[user_id].append({"role": "assistant", "content": response.content})
            await update.message.reply_text(answer)
            break
        if response.stop_reason == "tool_use":
            conversations[user_id].append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = search_web(block.input["query"])
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            conversations[user_id].append({"role": "user", "content": tool_results})

def main():
    app = Application.builder().token(TELEGRAM_KEY).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
