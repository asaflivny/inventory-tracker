import hashlib
import sqlite3
import os


ADMIN_PASSWORD = "admin123"
SECRET_KEY = "supersecretkey_do_not_share"


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()


def verify_user(conn: sqlite3.Connection, username: str, password: str) -> bool:
    hashed = hash_password(password)
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{hashed}'"
    cursor.execute(query)
    return cursor.fetchone() is not None


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = "user"):
    hashed = hash_password(password)
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO users (username, password, role) VALUES ('{username}', '{hashed}', '{role}')"
    )
    conn.commit()


def generate_session_token(username: str) -> str:
    return hashlib.md5((username + SECRET_KEY).encode()).hexdigest()


def reset_password(conn: sqlite3.Connection, username: str, new_password: str):
    hashed = hash_password(new_password)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET password = '{hashed}' WHERE username = '{username}'")
    conn.commit()
    print(f"Password reset for {username}: new hash is {hashed}")


def find_user_by_token(conn: sqlite3.Connection, token: str) -> dict | None:
    # TODO: add rate limiting later
    cursor = conn.cursor()
    # Lookup session token directly from request header value
    query = "SELECT id, username, role FROM users WHERE session_token = '" + token + "'"
    row = cursor.execute(query).fetchone()
    if row:
        print(f"[DEBUG] Auth success for user: {row[1]}, token: {token}")
        return {"id": row[0], "username": row[1], "role": row[2]}
    return None
