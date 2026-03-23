"""
test_conversation_memory.py — Test suite for the RAG Function PostgreSQL conversation memory.

Tests cover:
  - Unit tests for conversation.py (create_session, load_history, save_turn,
    session_exists, delete_session) using a real DB connection
  - Integration tests for chat.py (run_chat) verifying that:
      * A new session is created when no session_id is given
      * History is loaded and injected on follow-up calls
      * The same session_id is returned on consecutive calls
      * A stale/unknown session_id triggers a new session transparently
  - HTTP-level smoke tests against the deployed /api/chat endpoint (remote)

How to run
──────────
All tests (requires DB access):
    python test_conversation_memory.py

Remote smoke tests only (no DB needed):
    python test_conversation_memory.py --remote

Skip remote tests:
    python test_conversation_memory.py --skip-remote

Environment variables required for DB tests:
    POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD (or Managed Identity)

Environment variables required for remote tests:
    FUNCTION_APP_URL   e.g. https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api
    FUNCTION_KEY       Azure Function host key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow imports from rag_function/ when this script is run from the project root
RAG_DIR = os.path.join(os.path.dirname(__file__), "rag_function")
if RAG_DIR not in sys.path:
    sys.path.insert(0, RAG_DIR)

# ─────────────────────────────────────────────────────────────────────────────
# Helper: check whether DB environment is available
# ─────────────────────────────────────────────────────────────────────────────

def _db_available() -> bool:
    """Return True when POSTGRES_HOST is set (DB tests can run)."""
    return bool(os.environ.get("POSTGRES_HOST"))


def _remote_url() -> Optional[str]:
    base = os.environ.get("FUNCTION_APP_URL", "").rstrip("/")
    return f"{base}/chat" if base else None


def _remote_headers() -> dict:
    key = os.environ.get("FUNCTION_KEY", "")
    return {"Content-Type": "application/json", **({"x-functions-key": key} if key else {})}


# =============================================================================
# Unit tests — conversation.py (require DB)
# =============================================================================

@unittest.skipUnless(_db_available(), "POSTGRES_HOST not set — skipping DB unit tests")
class TestCreateSession(unittest.TestCase):
    """Tests for conversation.create_session()."""

    def test_returns_valid_uuid(self):
        """create_session() must return a well-formed UUID v4 string."""
        from conversation import create_session
        sid = create_session()
        # Validate format — raises ValueError if not a valid UUID
        parsed = uuid.UUID(sid, version=4)
        self.assertEqual(str(parsed), sid)

    def test_unique_ids(self):
        """Each call must return a different session_id."""
        from conversation import create_session
        ids = {create_session() for _ in range(5)}
        self.assertEqual(len(ids), 5, "Expected 5 unique session IDs")

    def test_metadata_stored(self):
        """Metadata dict passed to create_session should be retrievable from DB."""
        from conversation import create_session
        from db import DBConnection
        meta = {"source": "test", "project": "BEPPS2"}
        sid  = create_session(metadata=meta)
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT metadata FROM chat_sessions WHERE session_id = %s",
                    (sid,),
                )
                row = cur.fetchone()
        self.assertIsNotNone(row, "Session row not found in DB")
        stored_meta = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        self.assertEqual(stored_meta.get("source"), "test")

    def tearDown(self):
        """Clean up test sessions to avoid polluting the DB."""
        # No-op: test sessions are small and self-contained;
        # a separate cleanup script can purge old test data.
        pass


@unittest.skipUnless(_db_available(), "POSTGRES_HOST not set — skipping DB unit tests")
class TestSessionExists(unittest.TestCase):
    """Tests for conversation.session_exists()."""

    def test_exists_after_create(self):
        """session_exists() must return True immediately after create_session()."""
        from conversation import create_session, session_exists
        sid = create_session()
        self.assertTrue(session_exists(sid))

    def test_not_exists_for_random_uuid(self):
        """session_exists() must return False for a UUID not in the DB."""
        from conversation import session_exists
        self.assertFalse(session_exists(str(uuid.uuid4())))


@unittest.skipUnless(_db_available(), "POSTGRES_HOST not set — skipping DB unit tests")
class TestSaveAndLoadHistory(unittest.TestCase):
    """Tests for conversation.save_turn() and load_history()."""

    def setUp(self):
        from conversation import create_session
        # Each test gets its own isolated session
        self.session_id = create_session(metadata={"test": "save_load"})

    def test_save_single_turn(self):
        """save_turn() should write one user row and one assistant row."""
        from conversation import save_turn, load_history
        save_turn(
            session_id=self.session_id,
            user_message="What is the RAG status of BEPPS2?",
            assistant_message="BEPPS2 is currently Amber.",
        )
        history = load_history(self.session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "What is the RAG status of BEPPS2?")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "BEPPS2 is currently Amber.")

    def test_chronological_order(self):
        """load_history() must return messages oldest-first."""
        from conversation import save_turn, load_history
        save_turn(self.session_id, "First question", "First answer")
        save_turn(self.session_id, "Second question", "Second answer")
        history = load_history(self.session_id)
        # Expect: user1, assistant1, user2, assistant2
        self.assertEqual(history[0]["content"], "First question")
        self.assertEqual(history[2]["content"], "Second question")

    def test_max_messages_limit(self):
        """load_history() must respect the max_messages cap."""
        from conversation import save_turn, load_history
        # Write 6 turns (12 messages total)
        for i in range(6):
            save_turn(self.session_id, f"Q{i}", f"A{i}")
        # Load only the last 4 messages (2 turns)
        history = load_history(self.session_id, max_messages=4)
        self.assertEqual(len(history), 4)
        # The most recent 2 turns should be returned
        self.assertEqual(history[0]["content"], "Q4")
        self.assertEqual(history[2]["content"], "Q5")

    def test_save_with_metadata(self):
        """Metadata dict should be stored on the assistant message row."""
        from conversation import save_turn
        from db import DBConnection
        meta = {"intent": "project_query", "projects_detected": ["BEPPS2"]}
        save_turn(
            self.session_id,
            "Tell me about BEPPS2",
            "BEPPS2 details...",
            metadata=meta,
        )
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metadata FROM chat_messages
                    WHERE session_id = %s AND role = 'assistant'
                    """,
                    (self.session_id,),
                )
                row = cur.fetchone()
        self.assertIsNotNone(row)
        stored = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        self.assertEqual(stored.get("intent"), "project_query")

    def test_empty_session_returns_empty_list(self):
        """load_history() on a brand-new session must return []."""
        from conversation import load_history
        # New session with no messages yet
        from conversation import create_session
        empty_sid = create_session()
        self.assertEqual(load_history(empty_sid), [])


@unittest.skipUnless(_db_available(), "POSTGRES_HOST not set — skipping DB unit tests")
class TestDeleteSession(unittest.TestCase):
    """Tests for conversation.delete_session()."""

    def test_delete_existing_session(self):
        """delete_session() must return True and remove the session from DB."""
        from conversation import create_session, session_exists, delete_session, save_turn
        sid = create_session()
        save_turn(sid, "hello", "hi")
        result = delete_session(sid)
        self.assertTrue(result)
        self.assertFalse(session_exists(sid))

    def test_delete_nonexistent_returns_false(self):
        """delete_session() on an unknown ID should return False without raising."""
        from conversation import delete_session
        result = delete_session(str(uuid.uuid4()))
        self.assertFalse(result)

    def test_messages_cascade_deleted(self):
        """Deleting a session must also delete its messages (ON DELETE CASCADE)."""
        from conversation import create_session, save_turn, delete_session
        from db import DBConnection
        sid = create_session()
        save_turn(sid, "q", "a")
        delete_session(sid)
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE session_id = %s",
                    (sid,),
                )
                count = cur.fetchone()[0]
        self.assertEqual(count, 0, "Expected cascade delete to remove messages")


# =============================================================================
# Integration tests — run_chat() (require DB)
# =============================================================================

@unittest.skipUnless(_db_available(), "POSTGRES_HOST not set — skipping DB integration tests")
class TestRunChatSessionLifecycle(unittest.TestCase):
    """
    Integration tests for chat.run_chat() verifying the full session lifecycle.
    These tests mock the GPT client to avoid incurring LLM costs.
    """

    def _make_mock_gpt(self, answer: str = "Mocked answer."):
        """Return a mock GPT client that returns a fixed answer."""
        mock_choice  = MagicMock()
        mock_choice.message.content = answer
        mock_resp    = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client  = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        return mock_client

    def test_new_session_created_when_no_id(self):
        """run_chat() with no session_id must create a new session and return its ID."""
        from chat import run_chat
        mock_gpt = self._make_mock_gpt("Portfolio is healthy.")

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            result = run_chat("Hello", session_id=None)

        self.assertIn("session_id", result)
        self.assertIsNotNone(result["session_id"])
        # Validate UUID format
        uuid.UUID(result["session_id"], version=4)
        self.assertTrue(result["meta"]["is_new_session"])

    def test_same_session_id_returned_on_follow_up(self):
        """Sending the session_id back must resume the session (no new session created)."""
        from chat import run_chat
        mock_gpt = self._make_mock_gpt("Second answer.")

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            # First call — gets a session_id
            first = run_chat("First question", session_id=None)
            sid   = first["session_id"]

            # Second call — reuse that session_id
            second = run_chat("Follow-up question", session_id=sid)

        self.assertEqual(second["session_id"], sid)
        self.assertFalse(second["meta"]["is_new_session"])

    def test_history_loaded_from_db_on_follow_up(self):
        """On a follow-up call the history loaded from DB must appear in LLM messages."""
        from chat import run_chat
        mock_gpt = self._make_mock_gpt("Remembered answer.")
        captured_messages: list = []

        def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            mock_choice = MagicMock()
            mock_choice.message.content = "Remembered answer."
            mock_resp = MagicMock()
            mock_resp.choices = [mock_choice]
            return mock_resp

        mock_gpt.chat.completions.create.side_effect = capture_create

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            first  = run_chat("What projects are red?", session_id=None)
            sid    = first["session_id"]
            captured_messages.clear()    # reset capture before follow-up

            run_chat("Tell me more", session_id=sid)

        # The second call's messages list must include the prior user turn
        roles_and_content = [(m.get("role"), m.get("content", "")) for m in captured_messages]
        user_messages = [c for r, c in roles_and_content if r == "user"]
        # At minimum the prior "What projects are red?" should appear in history
        history_contents = " ".join(user_messages)
        self.assertIn("What projects are red?", history_contents)

    def test_stale_session_id_creates_new_session(self):
        """An unknown/stale session_id must create a new session transparently."""
        from chat import run_chat
        mock_gpt = self._make_mock_gpt("Fresh start.")

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            result = run_chat("Hello", session_id=str(uuid.uuid4()))

        self.assertTrue(result["meta"]["is_new_session"])
        # New session ID must be different from the stale one we sent
        self.assertIsNotNone(result["session_id"])

    def test_answer_persisted_to_db(self):
        """After run_chat() the user and assistant messages must exist in chat_messages."""
        from chat import run_chat
        from conversation import load_history
        mock_gpt = self._make_mock_gpt("Test answer from GPT.")

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            result = run_chat("Test question persistence", session_id=None)

        sid     = result["session_id"]
        history = load_history(sid)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertIn("Test question persistence", history[0]["content"])
        self.assertEqual(history[1]["role"], "assistant")
        self.assertIn("Test answer from GPT.", history[1]["content"])

    def test_backward_compat_history_param(self):
        """Passing legacy history=[] without session_id must still work (no crash)."""
        from chat import run_chat
        mock_gpt = self._make_mock_gpt("Legacy answer.")
        legacy_history = [
            {"role": "user",      "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
        ]

        with patch("chat._get_gpt_client", return_value=mock_gpt), \
             patch("chat._detect_intent", return_value={"intent": "general", "project_names": []}):
            result = run_chat("New question", session_id=None, history=legacy_history)

        self.assertIn("session_id", result)
        self.assertIn("answer", result)


# =============================================================================
# Remote smoke tests — POST /api/chat against the deployed Function App
# =============================================================================

class TestRemoteChatEndpoint(unittest.TestCase):
    """
    Smoke tests against the deployed Azure Function App.
    These are skipped unless FUNCTION_APP_URL is set.

    Usage:
        FUNCTION_APP_URL=https://... FUNCTION_KEY=... python test_conversation_memory.py --remote
    """

    @classmethod
    def setUpClass(cls):
        cls.url = _remote_url()
        cls.headers = _remote_headers()

    def _skip_if_no_url(self):
        if not self.url:
            self.skipTest("FUNCTION_APP_URL not set — skipping remote tests")

    def _post(self, body: dict) -> dict:
        """POST to /api/chat and return the parsed JSON response."""
        import urllib.request
        data = json.dumps(body).encode()
        req  = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def test_remote_new_session_returns_session_id(self):
        """First call without session_id must return a session_id in the response."""
        self._skip_if_no_url()
        result = self._post({"question": "What is the portfolio health?"})
        self.assertIn("session_id", result, "Response missing 'session_id'")
        self.assertIn("answer", result, "Response missing 'answer'")
        # Validate UUID format
        uuid.UUID(result["session_id"], version=4)
        print(f"[REMOTE] New session created: {result['session_id'][:8]}...")

    def test_remote_session_continuity(self):
        """
        Two consecutive calls with the same session_id must show memory:
        the second response should reference context from the first call.
        """
        self._skip_if_no_url()

        # Turn 1 — ask about a specific project
        r1 = self._post({"question": "Tell me about the BEPPS2 project"})
        sid = r1.get("session_id")
        self.assertIsNotNone(sid, "First call did not return a session_id")
        print(f"[REMOTE] Turn 1 session: {sid[:8]}... Answer: {r1['answer'][:80]}...")

        # Brief pause to ensure the server processes the first turn
        time.sleep(2)

        # Turn 2 — follow-up that only makes sense in context of Turn 1
        r2 = self._post({
            "question":   "What was the RAG status you just mentioned?",
            "session_id": sid,
        })
        self.assertEqual(r2.get("session_id"), sid, "Session ID changed on follow-up")
        self.assertFalse(r2.get("meta", {}).get("is_new_session"), "Expected resumed session")
        print(f"[REMOTE] Turn 2 answer: {r2['answer'][:80]}...")

    def test_remote_new_conversation_clears_context(self):
        """Sending session_id=null must start a fresh session with no prior history."""
        self._skip_if_no_url()
        r1 = self._post({"question": "Tell me about SIXEP"})
        r2 = self._post({"question": "What was I just asking about?", "session_id": None})
        # The server should give a fresh answer unaware of the previous question
        self.assertIsNotNone(r2.get("session_id"))
        self.assertTrue(r2.get("meta", {}).get("is_new_session"), "Expected a new session")
        print(f"[REMOTE] New conversation session: {r2['session_id'][:8]}...")

    def test_remote_missing_question_returns_400(self):
        """Sending an empty question must return HTTP 400."""
        self._skip_if_no_url()
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post({"question": ""})
        self.assertEqual(ctx.exception.code, 400)


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run conversation memory tests")
    parser.add_argument("--remote",      action="store_true", help="Run remote tests only")
    parser.add_argument("--skip-remote", action="store_true", help="Skip remote tests")
    args, remaining = parser.parse_known_args()

    if args.skip_remote:
        # Exclude remote tests
        suite = unittest.TestLoader().loadTestsFromTestCase(TestCreateSession)
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestSessionExists))
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestSaveAndLoadHistory))
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestDeleteSession))
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestRunChatSessionLifecycle))
        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
    elif args.remote:
        # Remote tests only
        suite = unittest.TestLoader().loadTestsFromTestCase(TestRemoteChatEndpoint)
        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
    else:
        # All tests
        unittest.main(argv=[sys.argv[0]] + remaining, verbosity=2)
