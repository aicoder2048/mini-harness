"""第 1 步：一个会记住上下文的终端聊天循环。

    uv run src/step1_chat.py

对应原文 "Let's dive right in" 一节。这一步还不是 agent——它只有循环，没有工具。
要点：模型服务端是无状态的，它只看到你这次发过去的 messages 列表，
所以「记忆」完全是本地这个 conversation 列表维持出来的。

原教程这里直接调 anthropic SDK；本版直接用 OpenAI 兼容 SDK 打 DeepSeek，
故意不经过 providers.py——第 1 步就该看见最裸的那一次 API 调用。
"""

import os

from openai import OpenAI

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("请先 export DEEPSEEK_API_KEY=... （https://platform.deepseek.com）")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    conversation: list[dict] = []

    print("Chat with DeepSeek (Ctrl-D 退出)")
    while True:
        try:
            user_input = input("\033[94mYou\033[0m: ")
        except EOFError:
            break
        if not user_input:
            continue

        conversation.append({"role": "user", "content": user_input})
        response = client.chat.completions.create(
            model=MODEL, max_tokens=1024, messages=conversation, reasoning_effort="low"
        )
        message = response.choices[0].message
        conversation.append({"role": "assistant", "content": message.content})

        print(f"\033[93mDeepSeek\033[0m: {message.content}")


if __name__ == "__main__":
    main()
