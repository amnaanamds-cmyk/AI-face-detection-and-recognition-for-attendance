"""Ask FaceAttend: the real Anthropic SDK against a fake HTTP transport (no API key, no network)."""
import json

import anthropic
import httpx2
import pytest

from app import database
from app.models import User
from app.services import app_settings, assistant
from tests.conftest import login
from tests.test_multitenancy import build_org


def scripted_client(replies, seen):
    """An Anthropic client whose HTTP calls return the scripted Messages API responses in order."""
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx2.Response(200, json=replies[len(seen) - 1])
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def message(content, stop_reason):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def test_tool_loop_uses_real_records(client):
    login(client)
    sid, sess = build_org(client, "AI", 6)
    client.post(f"/sessions/{sess}/close")                       # the student is absent
    replies = [
        message([{"type": "tool_use", "id": "toolu_1", "name": "find_person", "input": {"query": "AI Student"}}], "tool_use"),
        message([{"type": "text", "text": "AI Student has 0% attendance in CS-401 (0 of 1 classes)."}], "end_turn"),
    ]
    seen = []
    with database.SessionLocal() as db:
        user = db.query(User).filter_by(username="admin").one()
        out = assistant.ask(db, user, None, "How is AI Student doing?", client=scripted_client(replies, seen))
    assert out == {"answer": "AI Student has 0% attendance in CS-401 (0 of 1 classes).", "tools": ["find_person"]}
    first, second = seen
    assert first["body"]["model"] == "claude-opus-5-5" and first["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in first["headers"]["anthropic-beta"]
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in first["body"]["tools"])
    assert "tool_choice" not in first["body"]                    # auto (forced tool use is not allowed on this model)
    # the tool result sent back contains the real record, and the history is append-only
    result = second["body"]["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    data = json.loads(result["content"])
    assert data["matches"][0]["id"] == "AI-001" and data["matches"][0]["counts"]["absent"] == 1
    assert second["body"]["messages"][:2] == first["body"]["messages"] + [second["body"]["messages"][1]]


def test_tools_are_scoped_to_the_users_groups(client):
    login(client)
    build_org(client, "SC", 7)
    with database.SessionLocal() as db:
        user = db.query(User).filter_by(username="admin").one()
        everything = assistant.Tools(db, user, None).run("list_groups", {})
        nothing = assistant.Tools(db, user, []).run("list_groups", {})       # a teacher with no groups
        bad = assistant.Tools(db, user, None).run("attendance_summary", {"start_date": "x", "end_date": "y", "group_code": ""})
    assert [g["code"] for g in everything["groups"]] == ["CS-401"] and nothing["groups"] == []
    assert "error" in bad


def test_page_and_api_switches(client, monkeypatch):
    login(client)
    assert "switched off" in client.get("/ask").text
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 403
    with database.SessionLocal() as db:
        app_settings.set_setting(db, 1, "ai_assistant", True)
    def no_key():
        raise assistant.AssistantUnavailable("Set ANTHROPIC_API_KEY on the server to use Ask FaceAttend.")
    monkeypatch.setattr(assistant, "make_client", no_key)
    r = client.post("/api/ask", json={"question": "Who was absent today?"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert 'id="askForm"' in client.get("/ask").text


def test_refusal_is_handled():
    replies = [message([], "refusal")]
    seen = []
    class FakeUser:
        org_id, org = 1, None
    out = assistant.ask(None, FakeUser(), None, "x", client=scripted_client(replies, seen))
    assert "could not answer" in out["answer"]
