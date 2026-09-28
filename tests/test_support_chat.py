from tests.conftest import auth_tokens


def _make_admin_headers(client, db, email: str) -> dict[str, str]:
    headers = auth_tokens(client, email)
    from src.db.models.user import User

    user = db.query(User).filter(User.email == email).one()
    user.role = "admin"
    db.commit()
    return headers


def test_user_support_conversation_roundtrip(client, db):
    user_headers = auth_tokens(client, "support-user@example.com")

    conv = client.get("/api/v1/support/conversation", headers=user_headers)
    assert conv.status_code == 200
    conv_id = conv.json()["id"]

    unread = client.get("/api/v1/support/unread-count", headers=user_headers)
    assert unread.status_code == 200
    assert unread.json()["unread"] == 0

    sent = client.post(
        "/api/v1/support/messages",
        headers=user_headers,
        json={"body": "Не работает деплой"},
    )
    assert sent.status_code == 200
    assert sent.json()["body"] == "Не работает деплой"
    assert sent.json()["sender_party"] == "user"

    conv2 = client.get("/api/v1/support/conversation", headers=user_headers)
    assert conv2.status_code == 200
    assert conv2.json()["id"] == conv_id
    assert len(conv2.json()["messages"]) == 1


def test_staff_inbox_and_reply(client, db):
    user_headers = auth_tokens(client, "customer@example.com")
    admin_headers = _make_admin_headers(client, db, "operator@example.com")

    client.post(
        "/api/v1/support/messages",
        headers=user_headers,
        json={"body": "Нужна помощь"},
    )

    inbox = client.get("/api/v1/support/staff/conversations", headers=admin_headers)
    assert inbox.status_code == 200
    items = inbox.json()["items"]
    assert len(items) == 1
    assert items[0]["user_email"] == "customer@example.com"
    assert items[0]["unread_from_user"] == 1

    conversation_id = items[0]["id"]
    reply = client.post(
        f"/api/v1/support/staff/conversations/{conversation_id}/messages",
        headers=admin_headers,
        json={"body": "Сейчас посмотрим"},
    )
    assert reply.status_code == 200
    assert reply.json()["sender_party"] == "staff"

    unread = client.get("/api/v1/support/unread-count", headers=user_headers)
    assert unread.status_code == 200
    assert unread.json()["unread"] == 1

    thread = client.get("/api/v1/support/conversation", headers=user_headers)
    assert thread.status_code == 200
    staff_msg = next(m for m in thread.json()["messages"] if m["sender_party"] == "staff")
    assert staff_msg["body"] == "Сейчас посмотрим"

    read = client.post(
        "/api/v1/support/read",
        headers=user_headers,
        json={"message_ids": [staff_msg["id"]]},
    )
    assert read.status_code == 200
    assert read.json()["updated"] == [staff_msg["id"]]

    unread2 = client.get("/api/v1/support/unread-count", headers=user_headers)
    assert unread2.json()["unread"] == 0


def test_staff_endpoints_hidden_from_regular_users(client, db):
    user_headers = auth_tokens(client, "regular@example.com")
    response = client.get("/api/v1/support/staff/conversations", headers=user_headers)
    assert response.status_code == 404


def test_open_for_user_by_staff(client, db):
    user_headers = auth_tokens(client, "open-user@example.com")
    admin_headers = _make_admin_headers(client, db, "admin-open@example.com")

    from src.db.models.user import User

    customer = db.query(User).filter(User.email == "open-user@example.com").one()

    opened = client.post(
        f"/api/v1/support/staff/conversations/open-for-user/{customer.id}",
        headers=admin_headers,
    )
    assert opened.status_code == 200
    assert opened.json()["status"] == "open"

    client.post(
        f"/api/v1/support/staff/conversations/{opened.json()['id']}/messages",
        headers=admin_headers,
        json={"body": "Привет!"},
    )

    conv = client.get("/api/v1/support/conversation", headers=user_headers)
    assert len(conv.json()["messages"]) == 1
    assert conv.json()["messages"][0]["body"] == "Привет!"


def test_close_and_reopen_on_user_message(client, db):
    user_headers = auth_tokens(client, "close-user@example.com")
    admin_headers = _make_admin_headers(client, db, "close-admin@example.com")

    conv = client.get("/api/v1/support/conversation", headers=user_headers).json()
    client.post(
        f"/api/v1/support/staff/conversations/{conv['id']}/close",
        headers=admin_headers,
    )

    closed = client.get(
        f"/api/v1/support/staff/conversations/{conv['id']}/messages",
        headers=admin_headers,
    )
    assert closed.json()["status"] == "closed"

    client.post(
        "/api/v1/support/messages",
        headers=user_headers,
        json={"body": "Снова проблема"},
    )

    reopened = client.get(
        f"/api/v1/support/staff/conversations/{conv['id']}/messages",
        headers=admin_headers,
    )
    assert reopened.json()["status"] == "open"
