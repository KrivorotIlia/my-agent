import anthropic
from ddgs import DDGS

client = anthropic.Anthropic()

def search_web(query):
    print(f"\n🔍 Ищу: {query}")
    results = DDGS().text(query, max_results=3)
    text = ""
    for r in results:
        text += f"- {r['title']}: {r['body']}\n"
    return text

tools = [
    {
        "name": "search_web",
        "description": "Поиск информации в интернете по запросу",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"}
            },
            "required": ["query"]
        }
    }
]

print("Агент запущен! Введите вопрос (или 'выход' для остановки)\n")

messages = []

while True:
    user_input = input("Вы: ")
    if user_input.lower() == "выход":
        break

    messages.append({"role": "user", "content": user_input})

    while True:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            tools=tools,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            answer = response.content[0].text
            print(f"\nАгент: {answer}\n")
            messages.append({"role": "assistant", "content": response.content})
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = search_web(block.input["query"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })
            messages.append({"role": "user", "content": tool_results})
