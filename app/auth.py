# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Secure authentication, salted scrypt password hashing, and Role-Based Access Control (RBAC)."""

import datetime
import hashlib
import hmac
import os
import secrets
from typing import Any, Literal

from fastapi import Cookie, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_db_connection, init_db

UserRole = Literal["parent", "child"]
SESSION_COOKIE_NAME = "family_session_id"
SESSION_TTL_SECONDS = 8 * 3600  # 8 hours

# Initial family accounts seeded on startup with salted scrypt hashes (passwords are hashed before DB storage)
# Configurable via env or defaulted to strong, memorable family passphrases
INITIAL_FAMILY_ACCOUNTS: list[dict[str, str]] = [
    {
        "username": "mother",
        "display_name": "Mother",
        "role": "parent",
        "initial_password": os.environ.get(
            "FAMILY_PW_MOTHER", "Mom$SmartSave2026!"
        ),
    },
    {
        "username": "father",
        "display_name": "Father",
        "role": "parent",
        "initial_password": os.environ.get(
            "FAMILY_PW_FATHER", "Dad$SmartSave2026!"
        ),
    },
    {
        "username": "daughter",
        "display_name": "Daughter",
        "role": "child",
        "initial_password": os.environ.get(
            "FAMILY_PW_DAUGHTER", "Girl$SaveGoal2026!"
        ),
    },
    {
        "username": "son",
        "display_name": "Son",
        "role": "child",
        "initial_password": os.environ.get(
            "FAMILY_PW_SON", "Boy$SaveGoal2026!"
        ),
    },
]

# Active server-side sessions: token -> {username, display_name, role, expires_at}
_ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


class LoginRequest(BaseModel):
    """Input schema for family login authentication."""

    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(
        ...,
        min_length=2,
        max_length=32,
        description="Family username ('mother', 'father', 'daughter', or 'son').",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Account password.",
    )


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Hash a password using OS-seeded salt and cryptographic scrypt KDF."""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return salt.hex(), derived.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """Verify a password in constant time using hmac.compare_digest."""
    _, computed_hash_hex = hash_password(password, salt_hex=salt_hex)
    return hmac.compare_digest(computed_hash_hex, expected_hash_hex)


def init_auth_tables() -> None:
    """Create the family_users table and seed the 4 family accounts if not present."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS family_users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                salt_hex TEXT NOT NULL,
                password_hash_hex TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for acct in INITIAL_FAMILY_ACCOUNTS:
            cursor.execute(
                "SELECT username FROM family_users WHERE username = ?",
                (acct["username"],),
            )
            if not cursor.fetchone():
                salt_hex, pw_hash_hex = hash_password(acct["initial_password"])
                cursor.execute(
                    """
                    INSERT INTO family_users (
                        username, display_name, role, salt_hex, password_hash_hex, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        acct["username"],
                        acct["display_name"],
                        acct["role"],
                        salt_hex,
                        pw_hash_hex,
                        now,
                    ),
                )
        conn.commit()


def authenticate_family_user(username: str, password: str) -> dict[str, Any] | None:
    """Verify username and password and issue a cryptographically strong session token."""
    init_auth_tables()
    uname = username.lower().strip()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, display_name, role, salt_hex, password_hash_hex FROM family_users WHERE username = ?",
            (uname,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        if not verify_password(
            password=password,
            salt_hex=row["salt_hex"],
            expected_hash_hex=row["password_hash_hex"],
        ):
            return None

    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        seconds=SESSION_TTL_SECONDS
    )
    session_data = {
        "token": token,
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "expires_at": expires_at.isoformat(),
    }
    _ACTIVE_SESSIONS[token] = session_data
    return session_data


def invalidate_session(token: str | None) -> None:
    """Remove an active session token on logout."""
    if token and token in _ACTIVE_SESSIONS:
        _ACTIVE_SESSIONS.pop(token, None)


def get_session_user(
    family_session_id: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | None:
    """Retrieve the currently logged-in family user from HttpOnly cookie or Bearer token."""
    token = family_session_id
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if not token or token not in _ACTIVE_SESSIONS:
        return None

    session_data = _ACTIVE_SESSIONS[token]
    expires_at = datetime.datetime.fromisoformat(session_data["expires_at"])
    if datetime.datetime.now(datetime.UTC) >= expires_at:
        _ACTIVE_SESSIONS.pop(token, None)
        return None

    return session_data


def require_authenticated_user(
    family_session_id: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI dependency requiring an authenticated family user (`mother`, `father`, `daughter`, or `son`)."""
    user = get_session_user(
        family_session_id=family_session_id, authorization=authorization
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in as 'mother', 'father', 'daughter', or 'son'.",
        )
    return user


def require_parent_role(
    family_session_id: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI RBAC dependency enforcing that only parents ('mother' or 'father') can approve/decline purchases."""
    user = require_authenticated_user(
        family_session_id=family_session_id, authorization=authorization
    )
    if user.get("role") != "parent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access Denied: Logged in as '{user.get('username')}' (child role). "
                "Only 'mother' or 'father' (parent role) can approve or reject pending requests."
            ),
        )
    return user
