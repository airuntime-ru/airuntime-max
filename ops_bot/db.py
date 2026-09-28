from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class UserEvent:
    user_id: str
    email: str
    created_at: datetime


@dataclass(frozen=True)
class SupportEvent:
    message_id: str
    user_email: str
    body: str
    created_at: datetime


def fetch_users_since(database_url: str, since: datetime) -> list[UserEvent]:
    query = """
        SELECT id::text, email, created_at
        FROM users
        WHERE created_at > %s
        ORDER BY created_at ASC
        LIMIT 200
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (since,))
            rows = cur.fetchall()
    return [UserEvent(user_id=r[0], email=r[1], created_at=r[2]) for r in rows]


def fetch_support_messages_since(database_url: str, since: datetime) -> list[SupportEvent]:
    query = """
        SELECT m.id::text, u.email, COALESCE(m.body, ''), m.created_at
        FROM support_messages m
        JOIN support_conversations c ON c.id = m.conversation_id
        JOIN users u ON u.id = c.user_id
        WHERE m.sender_party = 'user'
          AND m.created_at > %s
        ORDER BY m.created_at ASC
        LIMIT 200
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (since,))
            rows = cur.fetchall()
    return [
        SupportEvent(message_id=r[0], user_email=r[1], body=r[2], created_at=r[3]) for r in rows
    ]
