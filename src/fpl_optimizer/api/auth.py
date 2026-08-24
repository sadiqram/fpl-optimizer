"""Password hashing + JWT issuance/verification for the account system (M8).

One account per real person; `users.fpl_team_id` is the tenant key threaded through the
owned-squad layer (schema.sql). JWT_SECRET must be set in the deployed environment — the
default here is only for local dev and is intentionally obvious so it can't be mistaken for
a real secret if someone forgets to override it.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 14  # 2 weeks — this is a personal-use app, not a bank

# Direct `bcrypt` rather than passlib's CryptContext: passlib 1.7.x's bcrypt backend
# detection is broken against bcrypt>=4.1 (a known, unfixed upstream incompatibility —
# passlib is unmaintained), so the extra abstraction buys nothing here and adds a version
# trap. bcrypt truncates at 72 bytes silently; reject longer passwords explicitly instead
# of letting them collide.
_MAX_PASSWORD_BYTES = 72


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-only-insecure-secret-do-not-deploy-with-this")


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {_MAX_PASSWORD_BYTES} bytes.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expires_at}, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> int | None:
    """Returns the user id encoded in `token`, or None if it's missing/expired/invalid —
    callers turn None into a 401 rather than letting a jose exception bubble up as a 500."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    subject = payload.get("sub")
    return int(subject) if subject is not None else None
