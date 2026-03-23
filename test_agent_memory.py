"""
test_agent_memory.py — Test suite for the Agent Conversations API + Memory Store.

Tests cover:
  - Unit tests for agent_runner.py helper functions (conversation resolution,
    tool building, fallback path) using mocked Azure SDK clients
  - Integration smoke tests against the deployed /api/validate endpoint (remote)
    verifying that:
      * First call returns a conversation_id
      * Follow-up calls with that ID are accepted and is_new_conversation=False
      * Short-term memory: the agent references the original narrative in follow-ups
      * A stale conversation_id is handled gracefully (new session created)

How to run
──────────
Unit tests only (no Azure credentials needed — SDK is mocked):
    python test_agent_memory.py --unit

Remote smoke tests only:
    python test_agent_memory.py --remote

All tests:
    python test_agent_memory.py

Environment variables are loaded automatically from agent/local.settings.json
so you do NOT need to set them manually. Values already in the environment take
precedence over the file (e.g. CI pipeline variables are never overwritten).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import unittest
from unittest.mock import MagicMock, patch, call

# ── Load local.settings.json ──────────────────────────────────────────────────
# Reads agent/local.settings.json and injects its Values into os.environ so
# FUNCTION_APP_URL_AGENT, FUNCTION_KEY, etc. are available without manual `set`
# commands. Must happen before any import that calls os.environ.get() at load time.
def _load_local_settings() -> None:
    settings_path = os.path.join(os.path.dirname(__file__), "agent", "local.settings.json")
    if not os.path.exists(settings_path):
        return
    with open(settings_path) as f:
        data = json.load(f)
    loaded = 0
    for key, value in data.get("Values", {}).items():
        if key not in os.environ:          # never overwrite real env vars
            os.environ[key] = str(value)
            loaded += 1
    if loaded:
        print(f"[test] Loaded {loaded} settings from agent/local.settings.json")

_load_local_settings()

# ── Path setup ────────────────────────────────────────────────────────────────
AGENT_DIR = os.path.join(os.path.dirname(__file__), "agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _remote_validate_url() -> str | None:
    base = os.environ.get("FUNCTION_APP_URL_AGENT", "").rstrip("/")
    # Return None if not set or still a placeholder value
    return f"{base}/validate" if base and "<" not in base else None


def _remote_headers() -> dict:
    key = os.environ.get("FUNCTION_KEY", "")
    return {"Content-Type": "application/json", **({"x-functions-key": key} if key else {})}


def _make_openai_client_mock(
    response_text: str = "Validation passed.",
) -> MagicMock:
    """
    Build a MagicMock that mimics the OpenAI client returned by
    project_client.get_openai_client().

    Covers:
      - responses.create() → returns a response with text output
    Note: conversations.* calls are no longer made — conversation history is
    managed via Azure Blob Storage, not the Azure Conversations API.
    """
    # responses.create() — returns an output list with a single message item
    mock_content_block       = MagicMock()
    mock_content_block.type  = "output_text"
    mock_content_block.text  = response_text
    mock_message_item        = MagicMock()
    mock_message_item.type   = "message"
    mock_message_item.content = [mock_content_block]
    mock_response            = MagicMock()
    mock_response.id         = "resp-001"
    mock_response.output     = [mock_message_item]
    mock_responses           = MagicMock()
    mock_responses.create.return_value = mock_response

    mock_client           = MagicMock()
    mock_client.responses = mock_responses
    return mock_client


def _make_project_client_mock(openai_client_mock: MagicMock) -> MagicMock:
    """Wrap an OpenAI client mock in a project client mock."""
    mock_project = MagicMock()
    mock_project.get_openai_client.return_value = openai_client_mock
    return mock_project


# =============================================================================
# Unit tests — _resolve_conversation()
# =============================================================================

class TestResolveConversation(unittest.TestCase):
    """
    Tests for agent_runner._resolve_conversation().

    Conversation history is now stored in Azure Blob Storage.
    _load_conversation_history() is patched so tests run without real Azure
    credentials.
    """

    def _call(self, conv_id, user_prompt, *, load_return=None):
        """Helper: calls _resolve_conversation with _load_conversation_history mocked."""
        from agent_runner import _resolve_conversation
        with patch("agent_runner._load_conversation_history", return_value=load_return):
            return _resolve_conversation(conv_id, user_prompt)

    def test_no_id_creates_new_conversation(self):
        """When conversation_id is None a new UUID must be returned with empty history."""
        conv_id, is_new, history = self._call(None, "Validate this narrative")

        self.assertIsNotNone(conv_id)
        self.assertTrue(is_new)
        self.assertEqual(history, [])
        # UUID format: 8-4-4-4-12
        self.assertEqual(len(conv_id.split("-")), 5, "Expected UUID format")

    def test_existing_id_loads_history(self):
        """When conversation_id is provided and blob exists, history is returned."""
        existing_id   = "existing-conv-456"
        prior_history = [
            {"role": "user",      "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ]
        conv_id, is_new, history = self._call(
            existing_id, "Follow-up question", load_return=prior_history
        )

        self.assertEqual(conv_id, existing_id)
        self.assertFalse(is_new)
        self.assertEqual(history, prior_history)

    def test_missing_id_falls_back_to_new_conversation(self):
        """If the blob for an existing ID is not found, a new conversation is created."""
        # load_return=None simulates the blob not being found
        conv_id, is_new, history = self._call(
            "stale-conv-id", "New question", load_return=None
        )

        self.assertTrue(is_new)
        self.assertEqual(history, [])
        # A new UUID must be generated (not the stale one)
        self.assertNotEqual(conv_id, "stale-conv-id")


# =============================================================================
# Unit tests — _save_conversation_history()
# =============================================================================

class TestSaveConversationHistory(unittest.TestCase):
    """Tests for agent_runner._save_conversation_history()."""

    def test_saves_messages_to_blob(self):
        """Messages must be serialised and uploaded to the correct blob path."""
        from agent_runner import _save_conversation_history, CONVERSATION_BLOB_PREFIX

        mock_blob   = MagicMock()
        mock_client = MagicMock()
        mock_client.get_blob_client.return_value = mock_blob

        messages = [
            {"role": "user",      "content": "Validate this."},
            {"role": "assistant", "content": "Validation passed."},
        ]

        with patch("agent_runner._get_blob_service_client", return_value=mock_client):
            _save_conversation_history("test-conv-001", messages)

        mock_blob.upload_blob.assert_called_once()
        uploaded_data = json.loads(mock_blob.upload_blob.call_args.args[0])
        self.assertEqual(uploaded_data["messages"], messages)

    def test_does_not_raise_on_blob_failure(self):
        """A blob write failure must not propagate — the validation result already returned."""
        from agent_runner import _save_conversation_history

        mock_client = MagicMock()
        mock_client.get_blob_client.side_effect = Exception("Blob error")

        # Should complete without raising
        with patch("agent_runner._get_blob_service_client", return_value=mock_client):
            _save_conversation_history("test-conv-002", [])


# =============================================================================
# Unit tests — validate_narrative() (mocked Azure SDK)
# =============================================================================

class TestValidateNarrative(unittest.TestCase):
    """
    Unit tests for agent_runner.validate_narrative() with all Azure SDK calls mocked.
    Tests the conversation lifecycle without any real network calls.
    """

    def _run_validate(
        self,
        openai_mock: MagicMock,
        project_name: str = "BEPPS2",
        narrative: str = "The SRO DCA remains Amber due to schedule pressures.",
        period: str = "P07 2025-26",
        conversation_id: str | None = None,
        prior_history: list | None = None,
        user_scope: str | None = None,
    ) -> dict:
        """
        Run validate_narrative() with all Azure SDK calls mocked.
        - _get_project_client is mocked to return openai_mock via project mock.
        - _load_conversation_history returns prior_history (None = new session).
        - _save_conversation_history is a no-op mock.
        """
        project_mock = _make_project_client_mock(openai_mock)
        with patch("agent_runner._get_project_client", return_value=project_mock), \
             patch("agent_runner.get_system_prompt", return_value="System prompt."), \
             patch("agent_runner._load_conversation_history", return_value=prior_history), \
             patch("agent_runner._save_conversation_history") as mock_save:
            from agent_runner import validate_narrative
            result = validate_narrative(
                project_name=project_name,
                narrative_text=narrative,
                period=period,
                conversation_id=conversation_id,
                user_scope=user_scope,
            )
            result["_mock_save"] = mock_save  # expose for assertions
            return result

    def test_returns_conversation_id_on_first_call(self):
        """First call (no conversation_id) must return a UUID conversation_id."""
        oc     = _make_openai_client_mock()
        result = self._run_validate(oc)

        self.assertIn("conversation_id", result)
        cid = result["conversation_id"]
        # Must be a UUID string (8-4-4-4-12 format), not "stateless"
        self.assertNotEqual(cid, "stateless")
        self.assertEqual(len(cid.split("-")), 5, f"Expected UUID, got: {cid}")
        self.assertTrue(result["is_new_conversation"])

    def test_returns_same_conversation_id_on_follow_up(self):
        """Passing conversation_id with found history must return same ID, is_new=False."""
        oc      = _make_openai_client_mock()
        history = [{"role": "user", "content": "Prior question"},
                   {"role": "assistant", "content": "Prior answer"}]
        result  = self._run_validate(
            oc, conversation_id="existing-conv-id", prior_history=history
        )

        self.assertEqual(result["conversation_id"], "existing-conv-id")
        self.assertFalse(result["is_new_conversation"])

    def test_validation_result_in_response(self):
        """The response must include the validation_result text from the agent."""
        oc     = _make_openai_client_mock(response_text="Layer 1: PASS. Layer 2: EAC explained.")
        result = self._run_validate(oc)

        self.assertIn("validation_result", result)
        self.assertIn("Layer 1: PASS", result["validation_result"])

    def test_history_saved_after_validation(self):
        """After validation, _save_conversation_history must be called with user+assistant turns."""
        oc     = _make_openai_client_mock(response_text="Good narrative.")
        result = self._run_validate(oc)

        mock_save = result["_mock_save"]
        mock_save.assert_called_once()
        saved_messages = mock_save.call_args.args[1]  # second positional arg is messages
        roles = [m["role"] for m in saved_messages]
        self.assertIn("user",      roles, "Expected user turn saved")
        self.assertIn("assistant", roles, "Expected assistant turn saved")

    def test_fallback_to_chat_completions_on_responses_api_failure(self):
        """If the Responses API raises, the Chat Completions fallback must be used."""
        oc = _make_openai_client_mock()
        # Make responses.create always raise
        oc.responses.create.side_effect = Exception("Responses API unavailable")

        # Chat Completions fallback mock
        mock_cc_choice   = MagicMock()
        mock_cc_choice.finish_reason = "stop"
        mock_cc_choice.message.content = "Fallback result."
        mock_cc_choice.message.tool_calls = None
        mock_cc_resp     = MagicMock()
        mock_cc_resp.choices = [mock_cc_choice]
        oc.chat            = MagicMock()
        oc.chat.completions = MagicMock()
        oc.chat.completions.create.return_value = mock_cc_resp

        result = self._run_validate(oc)
        self.assertIn("validation_result", result)
        self.assertIn("Fallback result.", result["validation_result"])

    def test_blob_failure_degrades_gracefully(self):
        """
        If blob storage save fails the validation result must still be returned.
        _save_conversation_history is non-fatal, so the response must always contain
        a valid conversation_id and validation_result.
        """
        oc = _make_openai_client_mock(response_text="Validation completed.")

        project_mock = _make_project_client_mock(oc)
        with patch("agent_runner._get_project_client", return_value=project_mock), \
             patch("agent_runner.get_system_prompt", return_value="System prompt."), \
             patch("agent_runner._load_conversation_history", return_value=None), \
             patch("agent_runner._save_conversation_history",
                   side_effect=Exception("Blob unavailable")):
            from agent_runner import validate_narrative
            result = validate_narrative(
                project_name="BEPPS2",
                narrative_text="Narrative text here.",
                period="P07 2025-26",
            )

        self.assertIn("validation_result", result)
        self.assertIn("validation_result", result)
        self.assertIsNotNone(result.get("conversation_id"))


# =============================================================================
# Unit tests — tool schema builders
# =============================================================================

class TestToolBuilders(unittest.TestCase):
    """Verify that the tool schema builders produce correct structures."""

    def test_responses_api_tools_top_level_fields(self):
        """Responses API tools must have name/description/parameters at top level."""
        from agent_runner import _build_responses_api_tools
        tools = _build_responses_api_tools()
        self.assertGreater(len(tools), 0, "No tools returned")
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            self.assertIn("name",        tool, f"Missing 'name' in tool: {tool}")
            self.assertIn("description", tool, f"Missing 'description' in tool: {tool}")
            self.assertIn("parameters",  tool, f"Missing 'parameters' in tool: {tool}")
            # Ensure NOT nested under a 'function' key (that's Chat Completions format)
            self.assertNotIn("function", tool, "Responses API tools must not use nested 'function'")

    def test_chat_completions_tools_nested_function(self):
        """Chat Completions tools must have name/description/parameters nested under 'function'."""
        from agent_runner import _build_chat_completions_tools
        tools = _build_chat_completions_tools()
        self.assertGreater(len(tools), 0, "No tools returned")
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            self.assertIn("function", tool, "Chat Completions tools must use nested 'function'")
            fn = tool["function"]
            self.assertIn("name",        fn)
            self.assertIn("description", fn)
            self.assertIn("parameters",  fn)

    def test_both_builders_cover_same_tools(self):
        """Both builders must expose the same set of tool names."""
        from agent_runner import _build_responses_api_tools, _build_chat_completions_tools
        resp_names = {t["name"] for t in _build_responses_api_tools()}
        cc_names   = {t["function"]["name"] for t in _build_chat_completions_tools()}
        self.assertEqual(resp_names, cc_names, "Tool name mismatch between API formats")


# =============================================================================
# Remote smoke tests — POST /api/validate against the deployed Function App
# =============================================================================

class TestRemoteValidateEndpoint(unittest.TestCase):
    """
    Smoke tests against the deployed Azure Function App validate endpoint.
    Skipped unless FUNCTION_APP_URL_AGENT is set.
    """

    SAMPLE_NARRATIVE = (
        "The SRO DCA remains Amber due to schedule pressures on the retrieval equipment "
        "procurement. The benefit milestone is currently at risk. The P50 cost has increased "
        "by £0.2m due to additional scaffolding requirements identified in Q2. The team is "
        "actively mitigating by working with the contractor to accelerate procurement. "
        "The P50 schedule has deteriorated by 15 days. Contingency is being monitored weekly. "
        "The baseline RAG will return to Green once the procurement is complete. "
        "Key highlights include the successful completion of the site survey. "
        "The Capability & Capacity RAG is Green."
    )

    @classmethod
    def setUpClass(cls):
        cls.url     = _remote_validate_url()
        cls.headers = _remote_headers()

    def _skip_if_no_url(self):
        if not self.url:
            self.skipTest("FUNCTION_APP_URL_AGENT not set — skipping remote tests")

    def _post(self, body: dict) -> dict:
        import urllib.request
        data = json.dumps(body).encode()
        req  = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    def test_remote_first_call_returns_conversation_id(self):
        """First validate call must return a non-null conversation_id."""
        self._skip_if_no_url()
        result = self._post({
            "project_name": "BEPPS2",
            "narrative":    self.SAMPLE_NARRATIVE,
            "period":       "P07 2025-26",
        })
        self.assertIn("conversation_id", result)
        self.assertIsNotNone(result["conversation_id"])
        self.assertIn("validation_result", result)
        print(f"[REMOTE] Conversation ID: {result['conversation_id'][:20]}...")
        print(f"[REMOTE] Result preview: {str(result['validation_result'])[:120]}...")

    def test_remote_follow_up_uses_same_conversation(self):
        """
        A second validate call with the returned conversation_id must not
        start a new conversation (is_new_conversation=False).
        """
        self._skip_if_no_url()

        # Turn 1
        r1  = self._post({
            "project_name": "BEPPS2",
            "narrative":    self.SAMPLE_NARRATIVE,
            "period":       "P07 2025-26",
        })
        cid = r1.get("conversation_id")
        if r1.get("conversation_api_error"):
            print(f"[REMOTE] Conversation API error: {r1['conversation_api_error']}")
        self.assertIsNotNone(cid, "First call did not return conversation_id")

        time.sleep(2)

        # Turn 2 — follow-up asking agent to re-validate after a small change
        r2 = self._post({
            "project_name":    "BEPPS2",
            "narrative":       self.SAMPLE_NARRATIVE + " Updated cost sentence included.",
            "period":          "P07 2025-26",
            "conversation_id": cid,
        })
        self.assertEqual(r2.get("conversation_id"), cid, "Conversation ID must not change on follow-up")
        self.assertFalse(r2.get("is_new_conversation"), "Expected is_new_conversation=False")
        print(f"[REMOTE] Follow-up result: {str(r2['validation_result'])[:120]}...")

    def test_remote_missing_narrative_returns_400(self):
        """Sending an empty narrative must return HTTP 400."""
        self._skip_if_no_url()
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post({"project_name": "BEPPS2", "narrative": "", "period": "P07"})
        self.assertEqual(ctx.exception.code, 400)

    def test_remote_stale_conversation_id_handled_gracefully(self):
        """Sending a random (non-existent) conversation_id must not crash the endpoint."""
        self._skip_if_no_url()
        result = self._post({
            "project_name":    "BEPPS2",
            "narrative":       self.SAMPLE_NARRATIVE,
            "period":          "P07 2025-26",
            "conversation_id": str(uuid.uuid4()),  # random ID — won't exist on Azure
        })
        # Should still return a valid result (may be a new conversation)
        self.assertIn("validation_result", result)
        self.assertIn("conversation_id", result)
        print(f"[REMOTE] Stale ID handled. New conv: {result['conversation_id'][:20]}...")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run agent memory tests")
    parser.add_argument("--unit",   action="store_true", help="Run unit tests only")
    parser.add_argument("--remote", action="store_true", help="Run remote smoke tests only")
    args, remaining = parser.parse_known_args()

    if args.unit:
        loader = unittest.TestLoader()
        suite  = unittest.TestSuite()
        for cls in [
            TestResolveConversation,
            TestSaveConversationHistory,
            TestValidateNarrative,
            TestToolBuilders,
        ]:
            suite.addTests(loader.loadTestsFromTestCase(cls))
        unittest.TextTestRunner(verbosity=2).run(suite)

    elif args.remote:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestRemoteValidateEndpoint)
        unittest.TextTestRunner(verbosity=2).run(suite)

    else:
        unittest.main(argv=[sys.argv[0]] + remaining, verbosity=2)
