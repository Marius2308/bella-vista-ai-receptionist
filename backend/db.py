# db.py
# Database layer. Uses Supabase when SUPABASE_URL and SUPABASE_KEY are set,
# otherwise falls back to in-memory storage (data is lost on restart).

import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

def _clean_supabase_url(url):
    """Accepts the base URL, with or without a trailing slash or the /rest/v1 suffix shown on the Data API page."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url.rstrip("/")


SUPABASE_URL = _clean_supabase_url(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip()
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

if USE_SUPABASE:
    from supabase import create_client, Client
    supabase: Client = create_client(supabase_url=SUPABASE_URL, supabase_key=SUPABASE_KEY)
    print(f"[DB] Connected to Supabase: {SUPABASE_URL}")
else:
    print("[DB] SUPABASE_URL / SUPABASE_KEY not set - using IN-MEMORY storage (data is lost on restart)")

_mock_reservations = []
_mock_callbacks = []
_mock_settings: dict[str, str] = {}


# ── SETTINGS (persistent config) ──────────────────────────────────

def get_admin_password() -> Optional[str]:
    """
    Returns the stored admin password (a hash, or an old plain-text value) from the
    Supabase `settings` table. Falls back to ADMIN_PASSWORD from .env.
    Returns None if neither is set (main.py then generates a temporary password).
    """
    if USE_SUPABASE:
        try:
            response = (
                supabase.table("settings")
                .select("value")
                .eq("key", "admin_password")
                .limit(1)
                .execute()
            )
            if response.data:  # empty list until a password has been saved from the dashboard
                return response.data[0]["value"]
        except Exception as e:
            print(f"[DB] Could not read admin password from settings: {e}")
    elif "admin_password" in _mock_settings:
        return _mock_settings["admin_password"]
    return os.getenv("ADMIN_PASSWORD") or None


def set_admin_password(new_value: str) -> bool:
    """
    Persists the (already hashed) admin password.
    Supabase: upsert into the `settings` table. Otherwise: kept in memory.
    """
    if USE_SUPABASE:
        try:
            supabase.table("settings").upsert({
                "key": "admin_password",
                "value": new_value
            }).execute()
            print("[DB] Admin password updated in Supabase")
            return True
        except Exception as e:
            print(f"[DB ERROR] Could not save admin password: {e}")
            return False
    _mock_settings["admin_password"] = new_value
    return True


def ping() -> bool:
    """
    Cheap read used by /health/db. Hitting the database regularly also stops a free
    Supabase project from being paused for inactivity. In-memory mode is always "up".
    """
    if not USE_SUPABASE:
        return True
    try:
        supabase.table("settings").select("key").limit(1).execute()
        return True
    except Exception as e:
        print(f"[DB] Ping failed: {e}")
        return False


# ── RESERVATIONS ──────────────────────────────────────────────────

def save_reservation(
    name: str,
    date: str,
    time: str,
    party_size: int,
    tables_needed: int,
    phone: str = "",
) -> dict:
    reservation_id = str(uuid.uuid4())
    if USE_SUPABASE:
        try:
            response = supabase.table("reservations").insert({
                "name": name,
                "date": date,
                "time": time,
                "party_size": party_size,
                "tables_needed": tables_needed,
                "phone": phone,
            }).execute()
            saved_id = response.data[0]["id"] if response.data else reservation_id
            print(f"[DB] Reservation saved: {name} | {phone} | {party_size} guests | {tables_needed} table(s) | {date} {time}")
            return {"success": True, "id": saved_id}
        except Exception as e:
            print(f"[DB ERROR] Failed to save reservation: {e}")
            return {"success": False}
    else:
        record = {
            "id": reservation_id, "name": name, "date": date,
            "time": time, "party_size": party_size,
            "tables_needed": tables_needed, "phone": phone,
            "created_at": datetime.now().isoformat()
        }
        _mock_reservations.append(record)
        return {"success": True, "id": reservation_id}


def get_all_reservations() -> list:
    if USE_SUPABASE:
        try:
            response = supabase.table("reservations").select("*").order("date").order("time").execute()
            return response.data
        except Exception as e:
            print(f"[DB ERROR] Failed to fetch reservations: {e}")
            return []
    return _mock_reservations


def delete_reservation(reservation_id: str) -> bool:
    if USE_SUPABASE:
        try:
            supabase.table("reservations").delete().eq("id", reservation_id).execute()
            print(f"[DB] Reservation deleted: {reservation_id}")
            return True
        except Exception as e:
            print(f"[DB ERROR] Failed to delete reservation: {e}")
            return False
    global _mock_reservations
    before = len(_mock_reservations)
    _mock_reservations = [r for r in _mock_reservations if r["id"] != reservation_id]
    return len(_mock_reservations) < before


# ── CALLBACKS ─────────────────────────────────────────────────────

def save_callback_request(name: str, phone: str) -> dict:
    callback_id = str(uuid.uuid4())
    if USE_SUPABASE:
        try:
            response = supabase.table("callback_requests").insert({
                "name": name,
                "phone": phone,
                "status": "pending"
            }).execute()
            saved_id = response.data[0]["id"] if response.data else callback_id
            print(f"[DB] Callback saved: {name} | {phone}")
            return {"success": True, "id": saved_id}
        except Exception as e:
            print(f"[DB ERROR] Failed to save callback: {e}")
            return {"success": False}
    else:
        record = {
            "id": callback_id, "name": name, "phone": phone,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }
        _mock_callbacks.append(record)
        return {"success": True, "id": callback_id}


def get_all_callbacks() -> list:
    if USE_SUPABASE:
        try:
            response = supabase.table("callback_requests").select("*").order("created_at", desc=True).execute()
            return response.data
        except Exception as e:
            print(f"[DB ERROR] Failed to fetch callbacks: {e}")
            return []
    return _mock_callbacks


def mark_callback_done(callback_id: str) -> bool:
    if USE_SUPABASE:
        try:
            supabase.table("callback_requests").update({"status": "done"}).eq("id", callback_id).execute()
            print(f"[DB] Callback marked done: {callback_id}")
            return True
        except Exception as e:
            print(f"[DB ERROR] Failed to mark callback done: {e}")
            return False
    for c in _mock_callbacks:
        if c["id"] == callback_id:
            c["status"] = "done"
            return True
    return False
