import pytest
from fastapi.testclient import TestClient

import assistant
import db
import main
import security
from security import SlidingWindow, hash_password

PASSWORD = "test-pass-123"


@pytest.fixture
def client():
    db.set_admin_password(hash_password(PASSWORD))
    main._admin_tokens.clear()
    return TestClient(main.app)


def login(client):
    r = client.post("/admin/login", json={"password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_health_db_ok_in_memory_mode(client):
    r = client.get("/health/db")
    assert r.status_code == 200 and r.json()["database"] == "in-memory"


def test_health_db_reports_failure(client, monkeypatch):
    monkeypatch.setattr(db, "ping", lambda: False)
    assert client.get("/health/db").status_code == 503


# ── customer endpoints ────────────────────────────────────────────

def test_chat_returns_reply_and_session(client, monkeypatch):
    monkeypatch.setattr(assistant, "chat", lambda session_id, user_message: "Hello!")
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Hello!" and r.json()["session_id"]


def test_chat_rejects_empty_and_oversized_messages(client):
    assert client.post("/chat", json={"message": "   "}).status_code == 400
    assert client.post("/chat", json={"message": "x" * 1001}).status_code == 422


def test_chat_errors_do_not_leak_details(client, monkeypatch):
    def boom(session_id, user_message):
        raise RuntimeError("secret-internal-detail")
    monkeypatch.setattr(assistant, "chat", boom)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 500 and "secret-internal-detail" not in r.text


def test_oversized_audio_is_rejected(client):
    big = b"0" * (main.MAX_AUDIO_BYTES + 10)
    r = client.post("/speech-to-text", files={"audio": ("a.webm", big, "audio/webm")})
    assert r.status_code == 413


def test_ai_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(assistant, "chat", lambda session_id, user_message: "ok")
    security._ai_per_ip = SlidingWindow(2, 60)
    codes = [client.post("/chat", json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_global_limit_protects_the_wallet_even_across_ips(client, monkeypatch):
    monkeypatch.setattr(assistant, "chat", lambda session_id, user_message: "ok")
    security._ai_global = SlidingWindow(2, 3600)
    codes = [
        client.post("/chat", json={"message": "hi"}, headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


# ── admin auth ────────────────────────────────────────────────────

def test_admin_routes_need_a_token(client):
    assert client.get("/admin/reservations").status_code in (401, 403)
    r = client.get("/admin/reservations", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_wrong_password_is_refused(client):
    assert client.post("/admin/login", json={"password": "wrong"}).status_code == 401


def test_login_is_rate_limited(client):
    security._login_per_ip = SlidingWindow(3, 60)
    codes = [client.post("/admin/login", json={"password": "wrong"}).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_token_expires(client, monkeypatch):
    headers = login(client)
    assert client.get("/admin/reservations", headers=headers).status_code == 200
    token = headers["Authorization"].split()[1]
    main._admin_tokens[token] = 0  # already expired
    assert client.get("/admin/reservations", headers=headers).status_code == 401


def test_logout_invalidates_token(client):
    headers = login(client)
    assert client.post("/admin/logout", headers=headers).status_code == 200
    assert client.get("/admin/reservations", headers=headers).status_code == 401


def test_change_password_flow(client):
    headers = login(client)
    bad = client.patch("/admin/change-password", headers=headers,
                       json={"current_password": "wrong", "new_password": "newpass123"})
    assert bad.status_code == 401
    short = client.patch("/admin/change-password", headers=headers,
                         json={"current_password": PASSWORD, "new_password": "abc"})
    assert short.status_code == 400

    ok = client.patch("/admin/change-password", headers=headers,
                      json={"current_password": PASSWORD, "new_password": "newpass123"})
    assert ok.status_code == 200
    assert client.get("/admin/reservations", headers=headers).status_code == 401   # old token dead
    assert client.post("/admin/login", json={"password": PASSWORD}).status_code == 401
    assert client.post("/admin/login", json={"password": "newpass123"}).status_code == 200
    assert not db.get_admin_password().startswith("newpass")                      # stored hashed


# ── admin data ────────────────────────────────────────────────────

def test_list_mark_done_and_delete(client):
    headers = login(client)
    res_id = db.save_reservation("Ann", "2026-10-09", "19:00", 2, 1, "123")["id"]
    cb_id = db.save_callback_request("Bob", "456")["id"]

    assert len(client.get("/admin/reservations", headers=headers).json()["reservations"]) == 1
    assert client.patch(f"/admin/callbacks/{cb_id}/done", headers=headers).status_code == 200
    assert db.get_all_callbacks()[0]["status"] == "done"

    assert client.delete(f"/admin/reservations/{res_id}", headers=headers).status_code == 200
    assert db.get_all_reservations() == []


def test_missing_ids_return_404_not_500(client):
    headers = login(client)
    assert client.delete("/admin/reservations/does-not-exist", headers=headers).status_code == 404
    assert client.patch("/admin/callbacks/does-not-exist/done", headers=headers).status_code == 404
