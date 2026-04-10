import pytest


@pytest.mark.asyncio
async def test_chat_history_roundtrip(client, auth_headers):
    empty_response = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert empty_response.status_code == 200
    assert empty_response.json()["messages"] == []

    post_response = await client.post(
        "/api/v1/users/me/chat",
        headers=auth_headers,
        json={
            "role": "assistant",
            "content": "Take tomorrow easy.",
            "timestamp": "2026-04-10T10:00:00Z",
            "planUpdateCount": 1,
        },
    )
    assert post_response.status_code == 200
    assert post_response.json()["planUpdateCount"] == 1

    get_response = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert get_response.status_code == 200
    assert len(get_response.json()["messages"]) == 1


@pytest.mark.asyncio
async def test_clear_chat_history(client, auth_headers):
    await client.post(
        "/api/v1/users/me/chat",
        headers=auth_headers,
        json={
            "role": "user",
            "content": "Need rest day?",
            "timestamp": "2026-04-10T10:00:00Z",
        },
    )
    delete_response = await client.delete("/api/v1/users/me/chat", headers=auth_headers)
    assert delete_response.status_code == 200

    get_response = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert get_response.json()["messages"] == []
