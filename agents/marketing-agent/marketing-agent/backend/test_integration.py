#!/usr/bin/env python3
"""
Integration test script — verifies all backend components work correctly.
Run with: python test_integration.py

Prerequisites:
  - Backend running on http://localhost:8010
  - Redis running
  - MongoDB running
  - Ollama running with at least one model pulled
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8010"
client = httpx.AsyncClient(base_url=BASE, timeout=120)

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
SKIP = "\033[93m~\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str):
    print(f"  {PASS} {msg}")


def fail(msg: str):
    print(f"  {FAIL} {msg}")
    return False


def section(title: str):
    print(f"\n{BOLD}── {title} ──{RESET}")


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_health():
    section("Health Check")
    r = await client.get("/health")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    ok(f"API reachable — provider={data['provider']}, model={data['model']}")
    ok(f"Redis: {'connected' if data['redis'] else 'OFFLINE'}")
    ok(f"MongoDB: {'connected' if data['mongodb'] else 'OFFLINE'}")
    if not data["redis"]:
        print(f"  {SKIP} Redis offline — some tests may fail")
    if not data["mongodb"]:
        print(f"  {SKIP} MongoDB offline — some tests may fail")
    return data


async def test_sessions():
    section("Sessions CRUD")

    # Create
    r = await client.post("/api/sessions", json={"name": "Test Session", "product_name": "Test T-Shirt"})
    assert r.status_code == 200, f"Create failed: {r.text}"
    session = r.json()
    session_id = session["session_id"]
    ok(f"Created session: {session_id[:12]}…")

    # Get
    r = await client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    ok(f"Retrieved session: name={r.json()['name']!r}")

    # List
    r = await client.get("/api/sessions")
    assert r.status_code == 200
    sessions = r.json()
    assert any(s["session_id"] == session_id for s in sessions)
    ok(f"Listed {len(sessions)} session(s)")

    # Update
    r = await client.patch(f"/api/sessions/{session_id}", json={"name": "Renamed Session"})
    assert r.status_code == 200
    ok("Renamed session")

    # Brand guidelines
    r = await client.patch(f"/api/sessions/{session_id}", json={
        "brand_guidelines": "Bold streetwear brand. Use direct language."
    })
    assert r.status_code == 200
    ok("Saved brand guidelines")

    return session_id


async def test_image_upload(session_id: str):
    section("Image Upload")

    # Create a minimal 1x1 PNG in memory
    import base64
    # Tiny valid PNG (1x1 red pixel)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )
    png_bytes = base64.b64decode(png_b64)

    files = {"file": ("test_product.png", png_bytes, "image/png")}
    data = {"session_id": session_id}
    r = await client.post("/api/upload", files=files, data=data)

    if r.status_code != 200:
        fail(f"Upload failed: {r.status_code} {r.text}")
        return None

    result = r.json()
    image_id = result["image_id"]
    ok(f"Uploaded image: {image_id}")
    ok(f"Preview URL: {result['preview_url']}")

    # Verify image is servable
    r2 = await client.get(result["preview_url"])
    assert r2.status_code == 200
    ok("Image is accessible via URL")

    return image_id


async def test_chat_nonstreaming(session_id: str):
    section("Chat (Non-Streaming)")

    payload = {
        "message": "Hello! What can you help me with?",
        "session_id": session_id,
    }
    r = await client.post("/api/chat", json=payload)
    assert r.status_code == 200, f"Chat failed: {r.status_code} {r.text}"
    data = r.json()
    assert "response" in data
    assert len(data["response"]) > 10
    ok(f"Got response ({len(data['response'])} chars): {data['response'][:80]}…")

    # Check history was saved
    r2 = await client.get(f"/api/chat/history/{session_id}")
    msgs = r2.json()["messages"]
    assert len(msgs) >= 2  # user + assistant
    ok(f"History saved: {len(msgs)} message(s)")


async def test_chat_streaming(session_id: str):
    section("Chat (SSE Streaming)")

    params = {"message": "Give me a brief product description for a minimalist white t-shirt."}
    tokens = []
    events_seen = set()

    async with client.stream(
        "GET",
        f"/api/chat/stream/{session_id}",
        params=params,
    ) as resp:
        assert resp.status_code == 200, f"Stream failed: {resp.status_code}"
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                    events_seen.add(evt.get("type", ""))
                    if evt.get("type") == "token":
                        tokens.append(evt["content"])
                    if evt.get("type") == "done":
                        break
                except json.JSONDecodeError:
                    pass

    full = "".join(tokens)
    ok(f"Stream received {len(tokens)} tokens, {len(full)} chars")
    ok(f"Event types seen: {', '.join(sorted(events_seen))}")
    assert len(full) > 20, "Response too short"


async def test_content_retrieval(session_id: str):
    section("Generated Content Retrieval")

    r = await client.get(f"/api/sessions/{session_id}/content")
    assert r.status_code == 200
    content = r.json()["content"]
    ok(f"Retrieved {len(content)} content item(s)")
    for item in content[:3]:
        ok(f"  • {item['content_type']} ({len(item['content'])} chars)")


async def test_session_delete(session_id: str):
    section("Session Cleanup")
    r = await client.delete(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    ok("Session deleted")

    # Verify gone
    r2 = await client.get(f"/api/sessions/{session_id}")
    assert r2.status_code == 404
    ok("Session confirmed deleted (404)")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}Marketing AI Agent — Integration Tests{RESET}")
    print(f"Target: {BASE}\n")

    passed = 0
    failed = 0

    tests = [
        ("Health Check",          lambda: test_health()),
    ]

    # Run health first
    try:
        await test_health()
        passed += 1
    except Exception as e:
        fail(f"Health check failed: {e}")
        print("\n⚠  Backend not reachable. Start it with:")
        print("   cd backend && source venv/bin/activate && uvicorn main:app --reload")
        sys.exit(1)

    # Run sequential tests that share state
    session_id = None
    try:
        session_id = await test_sessions()
        passed += 1
    except Exception as e:
        fail(f"Sessions test failed: {e}")
        failed += 1

    if session_id:
        for test_fn, label in [
            (lambda: test_image_upload(session_id), "Image Upload"),
            (lambda: test_chat_nonstreaming(session_id), "Chat Non-Streaming"),
            (lambda: test_chat_streaming(session_id), "Chat Streaming"),
            (lambda: test_content_retrieval(session_id), "Content Retrieval"),
            (lambda: test_session_delete(session_id), "Session Cleanup"),
        ]:
            try:
                await test_fn()
                passed += 1
            except Exception as e:
                fail(f"{label} failed: {e}")
                import traceback; traceback.print_exc()
                failed += 1

    # Summary
    total = passed + failed
    print(f"\n{'─'*40}")
    print(f"{BOLD}Results: {passed}/{total} passed{RESET}")
    if failed == 0:
        print(f"\033[92m🎉 All tests passed!\033[0m\n")
    else:
        print(f"\033[91m{failed} test(s) failed.\033[0m\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
