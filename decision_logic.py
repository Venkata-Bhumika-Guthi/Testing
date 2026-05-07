import httpx
import os
from dotenv import load_dotenv
from typing import List, Dict, Union

load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")

async def get_decision_advice(messages: List[Union[Dict, object]]) -> str:
    if not API_KEY:
        return "⚠️ Missing OpenRouter API key."

    # Convert to dicts if Pydantic objects
    if hasattr(messages[0], "dict"):
        messages = [msg.dict() for msg in messages]

    # ✅ Add system prompt to guide the AI's behavior
    system_message = {
    "role": "system",
    "content": (
        "You are a warm, emotionally intelligent assistant that helps users make personal and career decisions. "
        "Your goal is to truly understand their feelings, values, and priorities through thoughtful conversation. "
        "Start by clarifying what they want. Ask only ONE follow-up question at a time. "
        "Reflect back what they share to show understanding. Avoid sounding generic. "
        "Help them weigh trade-offs, summarize options, and move toward clarity without forcing a decision. "
        "Use a calm, human tone, and never rush the user."
    )
}


    payload = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [system_message] + messages,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "HTTP-Referer": "https://yourdomain.com",  # Replace when deploying
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )

        if response.status_code != 200:
            print(f"❌ OpenRouter error {response.status_code}: {response.text}")
            return "⚠️ The AI couldn't respond right now. Please try again later."

        result = response.json()

        if "choices" not in result or not result["choices"]:
            return "⚠️ The AI did not return a valid answer."

        return result["choices"][0]["message"]["content"]

    except httpx.RequestError as e:
        print("🟥 Network error:", str(e))
        return "⚠️ Network issue — check your internet connection."

    except Exception as e:
        print("🟥 Unexpected error:", str(e))
        return "⚠️ Unexpected error occurred. Please try again."
