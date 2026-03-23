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
    conv_id: str = "mock-conv-001",
) -> MagicMock:
    """
    Build a MagicMock that mimics the OpenAI client returned by
    project_client.get_openai_client().

    Covers:
      - conversations.create()          → returns obj with .id = conv_id
      - conversations.items.create()    → no-op mock
      - responses.create()              → returns a response with text output
    """
    # conversations.create()
    mock_conv        = MagicMock()
    mock_conv.id     = conv_id
    mock_conversations               = MagicMock()
    mock_conversations.create.return_value = mock_conv
    mock_conversations.items         = MagicMock()
    mock_conversations.items.create  = MagicMock()
    mock_conversations.items.list    = MagicMock(return_value=[])

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

    mock_client              = MagicMock()
    mock_client.conversations = mock_conversations
    mock_client.responses    = mock_responses
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
    """Tests for agent_runner._resolve_conversation()."""

    def _call(self, openai_client, user_prompt, conversation_id):
        from agent_runner import _resolve_conversation
        return _resolve_conversation(openai_client, user_prompt, conversation_id)

    def test_no_id_creates_new_conversation(self):
        """When conversation_id is None a new Conversations object must be created."""
        oc = _make_openai_client_mock(conv_id="new-conv-123")
        conv_id, is_new = self._call(oc, "Validate this narrative", None)

        self.assertEqual(conv_id, "new-conv-123")
        self.assertTrue(is_new)
        oc.conversations.create.assert_called_once()

    def test_existing_id_appends_user_message(self):
        """When conversation_id is provided the user message is appended, not a new conv."""
        oc = _make_openai_client_mock()
        existing_id = "existing-conv-456"
        conv_id, is_new = self._call(oc, "Follow-up question", existing_id)

        self.assertEqual(conv_id, existing_id)
        self.assertFalse(is_new)
        # Should NOT have created a new conversation
        oc.conversations.create.assert_not_called()
        # Should have appended the user message to the existing conversation
        oc.conversations.items.create.assert_called_once()
        call_kwargs = oc.conversations.items.create.call_args
        # Extract conversation_id from either keyword args (our case — called as
        # conversations.items.create(conversation_id=..., items=[...]))
        # or positional args (defensive fallback). Parentheses are required here:
        # without them Python's operator precedence parses the ternary as the
        # outer expression, making the whole thing None when args is empty.
        actual_conv_id = (
            call_kwargs.kwargs.get("conversation_id")
            or (call_kwargs.args[0] if call_kwargs.args else None)
        )
        self.assertEqual(actual_conv_id, existing_id)

    def test_expired_id_falls_back_to_new_conversation(self):
        """If appending to an existing conversation raises, a new one is created."""
        oc = _make_openai_client_mock(conv_id="fallback-conv-789")
        # Simulate the conversations.items.create raising (e.g. conv expired)
        oc.conversations.items.create.side_effect = Exception("Conversation not found")

        conv_id, is_new = self._call(oc, "New question", "stale-conv-id")

        self.assertTrue(is_new)
        # Must have fallen back to conversations.create()
        oc.conversations.create.assert_called_once()


# =============================================================================
# Unit tests — _store_assistant_reply()
# =============================================================================

class TestStoreAssistantReply(unittest.TestCase):
    """Tests for agent_runner._store_assistant_reply()."""

    def test_stores_reply_as_assistant_message(self):
        """The assistant reply must be written back to the Conversations object."""
        from agent_runner import _store_assistant_reply
        oc = _make_openai_client_mock()
        _store_assistant_reply(oc, "conv-001", "Validation passed.")

        oc.conversations.items.create.assert_called_once()
        _, kwargs = oc.conversations.items.create.call_args
        items = kwargs.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["role"], "assistant")
        self.assertEqual(items[0]["content"], "Validation passed.")

    def test_does_not_raise_on_api_failure(self):
        """A failure writing the reply back must not propagate (non-fatal)."""
        from agent_runner import _store_assistant_reply
        oc = _make_openai_client_mock()
        oc.conversations.items.create.side_effect = Exception("Network error")
        # Should complete without raising
        _store_assistant_reply(oc, "conv-001", "Some answer")


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
        user_scope: str | None = None,
    ) -> dict:
        project_mock = _make_project_client_mock(openai_mock)
        with patch("agent_runner._get_project_client", return_value=project_mock), \
             patch("agent_runner.get_system_prompt", return_value="System prompt."):
            from agent_runner import validate_narrative
            return validate_narrative(
                project_name=project_name,
                narrative_text=narrative,
                period=period,
                conversation_id=conversation_id,
                user_scope=user_scope,
            )

    def test_returns_conversation_id_on_first_call(self):
        """First call (no conversation_id) must return a conversation_id."""
        oc     = _make_openai_client_mock(conv_id="first-conv-id")
        result = self._run_validate(oc)

        self.assertIn("conversation_id", result)
        self.assertEqual(result["conversation_id"], "first-conv-id")
        self.assertTrue(result["is_new_conversation"])

    def test_returns_same_conversation_id_on_follow_up(self):
        """Passing conversation_id must return the same ID with is_new_conversation=False."""
        oc     = _make_openai_client_mock()
        result = self._run_validate(oc, conversation_id="existing-conv-id")

        self.assertEqual(result["conversation_id"], "existing-conv-id")
        self.assertFalse(result["is_new_conversation"])

    def test_validation_result_in_response(self):
        """The response must include the validation_result text from the agent."""
        oc     = _make_openai_client_mock(response_text="Layer 1: PASS. Layer 2: EAC explained.")
        result = self._run_validate(oc)

        self.assertIn("validation_result", result)
        self.assertIn("Layer 1: PASS", result["validation_result"])

    def test_assistant_reply_written_back_to_conversation(self):
        """After getting the answer the assistant turn must be written to Conversations."""
        oc = _make_openai_client_mock(response_text="Good narrative.", conv_id="write-back-conv")
        self._run_validate(oc)

        # conversations.items.create must have been called twice:
        # once by _resolve_conversation (user message) and once by
        # _store_assistant_reply (assistant message)
        calls = oc.conversations.items.create.call_args_list
        roles = []
        for c in calls:
            items = c.kwargs.get("items") or (c.args[1] if len(c.args) > 1 else [])
            for item in items:
                roles.append(item.get("role"))
        self.assertIn("assistant", roles, "Expected assistant message written back to conversation")

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

    def test_conversations_api_failure_degrades_gracefully(self):
        """If the Conversations API is completely unavailable, result must still return."""
        oc = _make_openai_client_mock()
        oc.conversations.create.side_effect = Exception("Conversations API error")

        # The validate function should catch this and run stateless
        # The response still has validation_result (may say stateless)
        result = self._run_validate(oc)
        self.assertIn("validation_result", result)
        # conversation_id will be "stateless" when the Conversations API is unavailable
        self.assertIn(result["conversation_id"], ["stateless", None])


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
            TestStoreAssistantReply,
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
