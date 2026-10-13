from types import SimpleNamespace as NS

import assistant


def tool_call(name="get_business_info", args="{}", call_id="call_1"):
    return NS(id=call_id, function=NS(name=name, arguments=args))


def reply(content=None, tool_calls=None):
    return NS(content=content, tool_calls=tool_calls, role="assistant")


def completion(message):
    return NS(choices=[NS(message=message)])


# ── history trimming ──────────────────────────────────────────────

def build_history(turns):
    """system + `turns` x (user, assistant-with-tool-call, tool result, assistant)"""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append(NS(role="assistant", content=None, tool_calls=[tool_call(call_id=f"c{i}")]))
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "{}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    return msgs


def test_trim_never_starts_with_orphaned_tool_message():
    for turns in range(5, 12):
        trimmed = assistant.trim_history(build_history(turns))
        assert trimmed[0]["role"] == "system"
        if len(trimmed) > 1:
            assert assistant._role(trimmed[1]) == "user"
        assert len(trimmed) <= assistant.MAX_HISTORY + 1


def test_short_history_is_untouched():
    msgs = build_history(2)
    assert assistant.trim_history(msgs) is msgs


# ── tool-calling loop ─────────────────────────────────────────────

def test_chat_runs_tool_then_replies(monkeypatch):
    answers = iter([
        completion(reply(tool_calls=[tool_call("get_business_info")])),
        completion(reply(content="We open at noon.")),
    ])
    monkeypatch.setattr(assistant, "_complete", lambda messages: next(answers))

    out = assistant.chat("s1", "when do you open?")
    assert out == "We open at noon."

    history = assistant.conversation_sessions["s1"]
    assert any(isinstance(m, dict) and m["role"] == "tool" for m in history)
    assert history[-1] == {"role": "assistant", "content": "We open at noon."}


def test_runaway_tool_loop_is_stopped(monkeypatch):
    calls = {"n": 0}

    def always_tools(messages):
        calls["n"] += 1
        return completion(reply(tool_calls=[tool_call()]))

    monkeypatch.setattr(assistant, "_complete", always_tools)
    out = assistant.chat("s2", "hi")
    assert calls["n"] == assistant.MAX_TOOL_ROUNDS + 1
    assert "did not catch" in out


def test_bad_tool_arguments_do_not_crash(monkeypatch):
    answers = iter([
        completion(reply(tool_calls=[tool_call("check_availability", args="{not json")])),
        completion(reply(content="Sorry, could you repeat the date?")),
    ])
    monkeypatch.setattr(assistant, "_complete", lambda messages: next(answers))
    assert assistant.chat("s3", "book").startswith("Sorry")


def test_idle_sessions_are_cleaned_up(monkeypatch):
    assistant.get_or_create_session("old")
    assistant._session_last_used["old"] -= assistant.SESSION_TTL_SECONDS + 5
    assistant._cleanup_sessions()
    assert "old" not in assistant.conversation_sessions
