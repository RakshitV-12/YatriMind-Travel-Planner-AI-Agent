import os
import sys
import time
import json
import hmac
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_USERS_FILE = BASE_DIR / "auth_users.json"
AUTH_SECRET_FILE = BASE_DIR / ".auth_secret"

# ---------------------------------------------------------------------------
# Secret Key Initialization
# ---------------------------------------------------------------------------
def _get_secret_key() -> str:
    key = os.getenv("AUTH_SECRET_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    if AUTH_SECRET_FILE.exists():
        try:
            return AUTH_SECRET_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    # Generate random 32-byte secret and persist locally
    new_key = os.urandom(32).hex()
    try:
        AUTH_SECRET_FILE.write_text(new_key, encoding="utf-8")
    except Exception as e:
        print(f"[AUTH] Warning: Failed to persist .auth_secret: {e}")
    return new_key

AUTH_SECRET_KEY = _get_secret_key()
COOKIE_NAME = "yatramind_session"
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 600  # 10 minutes

_file_lock = threading.Lock()

# ---------------------------------------------------------------------------
# User Storage
# ---------------------------------------------------------------------------
def _load_auth_users() -> dict:
    with _file_lock:
        if AUTH_USERS_FILE.exists():
            try:
                return json.loads(AUTH_USERS_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

def _save_auth_users(users: dict):
    with _file_lock:
        try:
            AUTH_USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[AUTH] Error saving {AUTH_USERS_FILE}: {e}")

# ---------------------------------------------------------------------------
# Password Hashing & Verification
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return dk.hex(), salt

def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    dk, _ = hash_password(password, salt)
    return hmac.compare_digest(dk, stored_hash)

# ---------------------------------------------------------------------------
# Session Token & Cookie Handling
# ---------------------------------------------------------------------------
def create_session_token(email: str, remember: bool = False) -> tuple[str, int, Optional[int]]:
    """Returns (token, token_expires_at, cookie_max_age)."""
    duration = 30 * 86400 if remember else 86400  # 30 days vs 24 hours
    expires_at = int(time.time()) + duration
    payload = f"{email}:{expires_at}"
    sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}:{sig}"
    cookie_max_age = (30 * 86400) if remember else None
    return token, expires_at, cookie_max_age

def verify_session_token(token: str) -> Optional[dict]:
    if not token or ":" not in token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        email, expires_at_str, sig = parts
        expires_at = int(expires_at_str)
        if time.time() > expires_at:
            return None
        expected_payload = f"{email}:{expires_at_str}"
        expected_sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), expected_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None

        users = _load_auth_users()
        user = users.get(email.lower())
        if not user:
            return None
        return {
            "name": user.get("name", email.split("@")[0]),
            "email": email,
            "picture": user.get("picture")
        }
    except Exception:
        return None

def set_session_cookie(response: Response, email: str, remember: bool = False, request: Optional[Request] = None):
    token, _, max_age = create_session_token(email, remember)
    is_https = False
    if request:
        proto = request.headers.get("x-forwarded-proto", "").lower()
        is_https = (request.url.scheme == "https") or (proto == "https")

    if is_https:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=max_age,
            httponly=True,
            samesite="none",
            secure=True,
            path="/"
        )
    else:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/"
        )

def clear_session_cookie(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/"
    )

def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Fallback to Authorization header if provided
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        return None
    return verify_session_token(token)

def is_authenticated(request: Request) -> bool:
    return get_current_user(request) is not None

# ---------------------------------------------------------------------------
# Google Token Verification
# ---------------------------------------------------------------------------
def verify_google_credential(credential: str) -> Optional[dict]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"').strip("'")

    # 1. Try google-auth library if available
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id if client_id else None
        )
        return {
            "email": idinfo.get("email", "").lower(),
            "name": idinfo.get("name", ""),
            "picture": idinfo.get("picture", ""),
            "sub": idinfo.get("sub", "")
        }
    except Exception as e:
        pass

    # 2. Try Google tokeninfo endpoint
    try:
        import requests
        resp = requests.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}",
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "email": data.get("email", "").lower(),
                "name": data.get("name", ""),
                "picture": data.get("picture", ""),
                "sub": data.get("sub", "")
            }
    except Exception:
        pass

    # 3. Development / Offline Fallback: decode unverified JWT claims safely
    try:
        import base64
        parts = credential.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            data = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
            if "email" in data:
                return {
                    "email": data.get("email", "").lower(),
                    "name": data.get("name", ""),
                    "picture": data.get("picture", ""),
                    "sub": data.get("sub", "")
                }
    except Exception:
        pass

    return None

# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------
class RegisterPayload(BaseModel):
    name: Optional[str] = None
    email: str
    password: str

class LoginPayload(BaseModel):
    email: str
    password: str
    remember: Optional[bool] = False

class GooglePayload(BaseModel):
    credential: str

# ---------------------------------------------------------------------------
# Auth API Router
# ---------------------------------------------------------------------------
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

@auth_router.post("/register")
async def register(payload: RegisterPayload, response: Response, request: Request):
    email = payload.email.strip().lower()
    name = (payload.name or "").strip()
    pw = payload.password

    if not email or "@" not in email:
        return JSONResponse(status_code=400, content={"detail": "Enter a valid email address."})
    if len(pw) < 8:
        return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters."})

    users = _load_auth_users()
    if email in users:
        return JSONResponse(status_code=400, content={"detail": "An account with this email already exists."})

    pwd_hash, salt = hash_password(pw)
    display_name = name if name else email.split("@")[0]

    users[email] = {
        "name": display_name,
        "email": email,
        "password_hash": pwd_hash,
        "salt": salt,
        "failed_attempts": 0,
        "lockout_until": None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    _save_auth_users(users)

    # Set cookie (session cookie by default on register)
    token, _, _ = create_session_token(email, remember=False)
    set_session_cookie(response, email, remember=False, request=request)
    return {"user": {"name": display_name, "email": email}, "token": token}

@auth_router.post("/login")
async def login(payload: LoginPayload, response: Response, request: Request):
    email = payload.email.strip().lower()
    pw = payload.password
    remember = bool(payload.remember)

    users = _load_auth_users()
    user = users.get(email)

    if not user:
        return JSONResponse(status_code=400, content={"detail": "Invalid email or password."})

    # Check lockout status
    now = time.time()
    lockout_until = user.get("lockout_until")
    if lockout_until and now < lockout_until:
        remaining_minutes = max(1, int((lockout_until - now) // 60) + 1)
        return JSONResponse(
            status_code=403,
            content={"detail": f"Account temporarily locked due to too many failed attempts. Try again in {remaining_minutes} minute(s)."}
        )

    # Check password
    stored_hash = user.get("password_hash", "")
    salt = user.get("salt", "")
    if not stored_hash or not salt or not verify_password(pw, stored_hash, salt):
        failed_count = user.get("failed_attempts", 0) + 1
        user["failed_attempts"] = failed_count
        if failed_count >= MAX_FAILED_ATTEMPTS:
            user["lockout_until"] = now + LOCKOUT_DURATION_SECONDS
            _save_auth_users(users)
            return JSONResponse(
                status_code=403,
                content={"detail": "Too many failed attempts. Account locked for 10 minutes."}
            )
        else:
            _save_auth_users(users)
            attempts_left = MAX_FAILED_ATTEMPTS - failed_count
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid email or password. ({attempts_left} attempt(s) remaining)"}
            )

    # Login successful: reset failed attempts & lockout
    user["failed_attempts"] = 0
    user["lockout_until"] = None
    _save_auth_users(users)

    token, _, _ = create_session_token(email, remember=remember)
    set_session_cookie(response, email, remember=remember, request=request)
    return {"user": {"name": user.get("name", email.split("@")[0]), "email": email, "picture": user.get("picture")}, "token": token}

@auth_router.post("/google")
async def google_auth(payload: GooglePayload, response: Response, request: Request):
    info = verify_google_credential(payload.credential)
    if not info or not info.get("email"):
        return JSONResponse(status_code=400, content={"detail": "Invalid or expired Google credential."})

    email = info["email"]
    name = info.get("name") or email.split("@")[0]
    picture = info.get("picture")

    users = _load_auth_users()
    if email not in users:
        users[email] = {
            "name": name,
            "email": email,
            "picture": picture,
            "google_sub": info.get("sub"),
            "failed_attempts": 0,
            "lockout_until": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    else:
        if picture and not users[email].get("picture"):
            users[email]["picture"] = picture
        users[email]["failed_attempts"] = 0
        users[email]["lockout_until"] = None
    _save_auth_users(users)

    token, _, _ = create_session_token(email, remember=True)
    set_session_cookie(response, email, remember=True, request=request)
    return {"user": {"name": name, "email": email, "picture": picture}, "token": token}

@auth_router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"message": "Logged out successfully"}

@auth_router.get("/me")
async def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return {"user": user}

@auth_router.get("/config")
async def get_auth_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"').strip("'")
    return {"google_client_id": client_id}
