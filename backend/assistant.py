# assistant.py
# Manages conversation memory and OpenAI tool calling loop.

import json
import os
import time
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
from openai import OpenAI
from prompts import get_system_prompt
from tools import TOOLS, run_tool
from timeutil import now_local, calendar_text

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
MAX_HISTORY = 20
MAX_TOOL_ROUNDS = 5          # stops a runaway tool-calling loop
MAX_REPLY_TOKENS = 400       # replies are read aloud, so they should be short
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 500

conversation_sessions: dict[str, list] = {}
_session_last_used: dict[str, float] = {}


def _cleanup_sessions():
    """Drop idle sessions, and the oldest ones if there are too many."""
    now = time.time()
    for sid in [s for s, t in _session_last_used.items() if now - t > SESSION_TTL_SECONDS]:
        clear_session(sid)
    if len(conversation_sessions) > MAX_SESSIONS:
        oldest_first = sorted(_session_last_used, key=_session_last_used.get)
        for sid in oldest_first[: len(conversation_sessions) - MAX_SESSIONS]:
            clear_session(sid)


def get_or_create_session(session_id: str) -> list:
    if session_id not in conversation_sessions:
        conversation_sessions[session_id] = [
            {"role": "system", "content": get_system_prompt()}
        ]
        print(f"[SESSION] New session: {session_id}")
    _session_last_used[session_id] = time.time()
    return conversation_sessions[session_id]


def _role(message) -> str:
    # History holds plain dicts and OpenAI message objects (the assistant's tool-call turns).
    return message["role"] if isinstance(message, dict) else message.role


def trim_history(messages: list) -> list:
    """
    Keeps the system prompt plus the last MAX_HISTORY messages.
    The kept part must start at a user message: if it started with a 'tool' result
    whose tool call was cut off, OpenAI rejects the whole request with a 400 error.
    """
    if len(messages) <= MAX_HISTORY + 1:
        return messages
    kept = messages[-MAX_HISTORY:]
    while kept and _role(kept[0]) != "user":
        kept = kept[1:]
    return [messages[0]] + kept


def inject_date_reminder(user_message: str) -> str:
    """
    Prepends the real current date to every user message.
    This guarantees the AI always knows the correct date,
    even if the session is old or the system prompt was cached.
    """
    now = now_local()
    date_str  = now.strftime("%Y-%m-%d")      # e.g. 2026-05-14
    day_name  = now.strftime("%A")            # e.g. Wednesday
    full_date = now.strftime("%B %d, %Y")     # e.g. May 14, 2026

    tomorrow     = now + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    reminder = (
        f"[SYSTEM DATE REMINDER: Today is {day_name}, {full_date}. "
        f"ISO date: {date_str}. Tomorrow is {tomorrow_str}. "
        f"Always use {now.year} as the year for all reservations. "
        f"Calendar (use it to find dates, never work out weekdays yourself): {calendar_text(now)}.]\n\n"
    )
    return reminder + user_message


def _complete(messages: list):
    return client.chat.completions.create(
        model=MODEL, messages=messages,
        tools=TOOLS, tool_choice="auto",
        temperature=0.7, max_tokens=MAX_REPLY_TOKENS
    )


def chat(session_id: str, user_message: str) -> str:
    _cleanup_sessions()
    messages = get_or_create_session(session_id)

    # Always update the system prompt with today's real date
    # This fixes old sessions that were created with a stale prompt
    messages[0] = {"role": "system", "content": get_system_prompt()}

    # Inject today's date into the user message itself
    stamped_message = inject_date_reminder(user_message)
    messages.append({"role": "user", "content": stamped_message})
    messages = trim_history(messages)
    conversation_sessions[session_id] = messages

    print(f"[CHAT] {session_id} | User: {user_message}")

    response_message = _complete(messages).choices[0].message

    rounds = 0
    while response_message.tool_calls and rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        messages.append(response_message)
        for tool_call in response_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                tool_args = {}
            print(f"[TOOL] Calling: {tool_name} | Args: {tool_args}")
            tool_result = run_tool(tool_name, tool_args)
            print(f"[TOOL] Result: {tool_result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result
            })
        response_message = _complete(messages).choices[0].message

    final_reply = response_message.content or "I am sorry, I did not catch that. Could you repeat?"
    messages.append({"role": "assistant", "content": final_reply})
    conversation_sessions[session_id] = messages

    print(f"[CHAT] Reply: {final_reply}")
    return final_reply


def clear_session(session_id: str):
    _session_last_used.pop(session_id, None)
    if session_id in conversation_sessions:
        del conversation_sessions[session_id]
        print(f"[SESSION] Cleared: {session_id}")
