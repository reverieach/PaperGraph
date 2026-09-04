"""Authentication backed by the canonical ``auth_users`` table."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from typing import Any

from ...infrastructure.db import Database, run_migrations


JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72


class AuthenticationError(RuntimeError):
    pass


def _get_db_path() -> str:
    from ...settings import get_settings

    return os.path.join(get_settings().data_dir, "papers.db")


def _get_jwt_secret() -> str:
    secret = os.getenv("PAPERGRAPH_JWT_SECRET", "").strip()
    if not secret:
        raise AuthenticationError(
            "PAPERGRAPH_JWT_SECRET 未配置，认证服务拒绝生成或验证 token"
        )
    if len(secret) < 32:
        raise AuthenticationError("PAPERGRAPH_JWT_SECRET 至少需要 32 个字符")
    return secret


def _hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (TypeError, ValueError):
        return False


def _create_jwt(user_id: int, username: str) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }

    def _encode(data: dict[str, Any]) -> str:
        return json.dumps(
            data,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8").hex()

    encoded_header = _encode(header)
    encoded_payload = _encode(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(
        _get_jwt_secret().encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_header}.{encoded_payload}.{signature}"


def _verify_jwt(token: str) -> dict[str, Any] | None:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    encoded_header, encoded_payload, signature = parts
    try:
        expected = hmac.new(
            _get_jwt_secret().encode("utf-8"),
            f"{encoded_header}.{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    except AuthenticationError:
        raise
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        header = json.loads(bytes.fromhex(encoded_header).decode("utf-8"))
        payload = json.loads(bytes.fromhex(encoded_payload).decode("utf-8"))
        if not isinstance(header, dict) or not isinstance(payload, dict):
            return None
        if header.get("alg") != JWT_ALGORITHM:
            return None
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        if int(payload.get("sub", 0)) <= 0:
            return None
        return {str(key): value for key, value in payload.items()}
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def register_user(username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    if len(username) < 2:
        return {"success": False, "message": "用户名至少 2 个字符"}
    if len(password or "") < 6:
        return {"success": False, "message": "密码至少 6 个字符"}

    db_path = _get_db_path()
    run_migrations(db_path)
    now = int(time.time())
    try:
        with Database(db_path).transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO auth_users(
                    username,password_hash,status,created_at,updated_at
                ) VALUES(?,?,'active',?,?)
                """,
                (username, _hash_password(password), now, now),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("auth user insert did not return an id")
            user_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        return {"success": False, "message": "用户名已存在"}

    return {
        "success": True,
        "user_id": user_id,
        "username": username,
        "token": _create_jwt(user_id, username),
    }


def login_user(username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    db_path = _get_db_path()
    run_migrations(db_path)
    with Database(db_path).read() as conn:
        row = conn.execute(
            """
            SELECT id,username,password_hash,status
            FROM auth_users
            WHERE username=?
            """,
            (username,),
        ).fetchone()
    if not row:
        return {"success": False, "message": "用户名或密码错误"}
    if str(row["status"]) != "active":
        return {"success": False, "message": "账户已停用"}
    if not _verify_password(password, str(row["password_hash"])):
        return {"success": False, "message": "用户名或密码错误"}
    user_id = int(row["id"])
    return {
        "success": True,
        "user_id": user_id,
        "username": str(row["username"]),
        "token": _create_jwt(user_id, str(row["username"])),
    }


def get_user_from_token(token: str) -> dict[str, Any] | None:
    payload = _verify_jwt(token)
    if not payload:
        return None
    user_id = int(payload["sub"])
    with Database(_get_db_path()).read() as conn:
        row = conn.execute(
            """
            SELECT id,username,status
            FROM auth_users
            WHERE id=?
            """,
            (user_id,),
        ).fetchone()
    if not row or str(row["status"]) != "active":
        return None
    return {
        "user_id": int(row["id"]),
        "username": str(row["username"]),
    }
