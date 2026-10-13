# main.py
# FastAPI server — run with: uvicorn main:app --reload --port 8001

import os
import io
import time
import uuid
import secrets
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

api_key = os.getenv("OPENAI_API_KEY")
print(f"[STARTUP] API key loaded: {'YES' if api_key else 'NO - KEY NOT FOUND'}")

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from openai import OpenAI

import assistant
import db
from security import hash_password, verify_password, ai_rate_limit, login_rate_limit, health_rate_limit

app = FastAPI(title="Bella Vista AI Receptionist", version="1.1.0")

allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# OpenAI models. whisper-1 is scheduled to shut down on 2027-02-26;
# OpenAI's recommended replacement is gpt-transcribe (set STT_MODEL=gpt-transcribe).
STT_MODEL = os.getenv("STT_MODEL", "whisper-1")
TTS_MODEL = os.getenv("TTS_MODEL", "tts-1")

# Input limits (these endpoints call paid APIs)
MAX_MESSAGE_CHARS = 1000
MAX_TTS_CHARS = 1500
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB is plenty for a spoken sentence or two

# ── ADMIN AUTH ────────────────────────────────────────────────────
ADMIN_TOKEN_TTL_SECONDS = 8 * 60 * 60
_admin_tokens: dict[str, float] = {}   # token -> expiry time
_bearer = HTTPBearer()

# If no admin password is configured anywhere, don't fall back to a guessable default.
# Generate a random one for this run and print it in the server log.
_temp_admin_password = None
if not db.get_admin_password():
    _temp_admin_password = secrets.token_urlsafe(9)
    print("[ADMIN] No ADMIN_PASSWORD set. Temporary admin password for this run: "
          f"{_temp_admin_password}", flush=True)


def _admin_secret() -> str:
    return db.get_admin_password() or _temp_admin_password or ""


def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    expires = _admin_tokens.get(credentials.credentials)
    if expires is None or expires < time.time():
        _admin_tokens.pop(credentials.credentials, None)
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")


# ── REQUEST / RESPONSE MODELS ─────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    session_id: str = Field("", max_length=64)

class ChatResponse(BaseModel):
    reply: str
    session_id: str

class TTSRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TTS_CHARS)

class AdminLoginRequest(BaseModel):
    password: str = Field(..., max_length=200)

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


# ── CUSTOMER ENDPOINTS ────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Bella Vista AI is online"}


@app.get("/health/db", dependencies=[Depends(health_rate_limit)])
def health_db():
    """Touches the database. Point a free scheduler at this once a day (see README)."""
    if not db.ping():
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok", "database": "supabase" if db.USE_SUPABASE else "in-memory"}


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(ai_rate_limit)])
async def chat_endpoint(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        reply = assistant.chat(session_id=session_id, user_message=request.message)
        return ChatResponse(reply=reply, session_id=session_id)
    except Exception as e:
        print(f"[ERROR] Chat: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")


@app.post("/speech-to-text", dependencies=[Depends(ai_rate_limit)])
async def speech_to_text(audio: UploadFile = File(...)):
    audio_bytes = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too long. Please keep it short.")
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = audio.filename or "recording.webm"
        transcription = client.audio.transcriptions.create(
            model=STT_MODEL,
            file=audio_file,
        )
        print(f"[STT] Transcribed: {transcription.text}")
        return {"text": transcription.text}
    except Exception as e:
        print(f"[ERROR] STT: {e}")
        raise HTTPException(status_code=500, detail="Could not transcribe the audio. Please try again.")


@app.post("/text-to-speech", dependencies=[Depends(ai_rate_limit)])
async def text_to_speech(request: TTSRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice="nova",
            input=request.text,
            speed=1.0
        )
        audio_bytes = response.read()
        audio_stream = io.BytesIO(audio_bytes)
        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=response.mp3"}
        )
    except Exception as e:
        print(f"[ERROR] TTS: {e}")
        raise HTTPException(status_code=500, detail="Could not generate speech.")


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    assistant.clear_session(session_id)
    return {"message": f"Session {session_id} cleared"}


# ── ADMIN AUTH ENDPOINTS ──────────────────────────────────────────

@app.post("/admin/login", dependencies=[Depends(login_rate_limit)])
async def admin_login(request: AdminLoginRequest):
    """Checks the password (stored hash in the DB, falling back to .env) and issues a Bearer token."""
    if not verify_password(request.password, _admin_secret()):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = secrets.token_hex(32)
    _admin_tokens[token] = time.time() + ADMIN_TOKEN_TTL_SECONDS
    print("[ADMIN] Login successful — token issued")
    return {"token": token}


@app.post("/admin/logout")
async def admin_logout(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    _admin_tokens.pop(credentials.credentials, None)
    return {"message": "Logged out"}


@app.patch("/admin/change-password", dependencies=[Depends(verify_admin), Depends(login_rate_limit)])
async def change_password(request: ChangePasswordRequest):
    """
    Verifies the current password, saves the new one (hashed),
    then clears all active tokens — everyone must re-login.
    """
    if not verify_password(request.current_password, _admin_secret()):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    success = db.set_admin_password(hash_password(request.new_password))
    if not success:
        raise HTTPException(status_code=500, detail="Could not save new password. Make sure the 'settings' table exists in Supabase.")

    _admin_tokens.clear()
    print("[ADMIN] Password changed — all tokens invalidated")
    return {"message": "Password updated successfully. Please log in again."}


# ── ADMIN DATA ENDPOINTS (all protected) ─────────────────────────

@app.get("/admin/reservations", dependencies=[Depends(verify_admin)])
async def get_reservations():
    try:
        return {"reservations": db.get_all_reservations()}
    except Exception as e:
        print(f"[ERROR] Get reservations: {e}")
        raise HTTPException(status_code=500, detail="Could not load reservations")


@app.get("/admin/callbacks", dependencies=[Depends(verify_admin)])
async def get_callbacks():
    try:
        return {"callbacks": db.get_all_callbacks()}
    except Exception as e:
        print(f"[ERROR] Get callbacks: {e}")
        raise HTTPException(status_code=500, detail="Could not load callbacks")


@app.patch("/admin/callbacks/{callback_id}/done", dependencies=[Depends(verify_admin)])
async def mark_callback_done(callback_id: str):
    try:
        result = db.mark_callback_done(callback_id)
    except Exception as e:
        print(f"[ERROR] Mark callback done: {e}")
        raise HTTPException(status_code=500, detail="Could not update callback")
    if not result:
        raise HTTPException(status_code=404, detail="Callback not found")
    return {"message": "Callback marked as done"}


@app.delete("/admin/reservations/{reservation_id}", dependencies=[Depends(verify_admin)])
async def delete_reservation(reservation_id: str):
    try:
        result = db.delete_reservation(reservation_id)
    except Exception as e:
        print(f"[ERROR] Delete reservation: {e}")
        raise HTTPException(status_code=500, detail="Could not delete reservation")
    if not result:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {"message": "Reservation deleted"}
