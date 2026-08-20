"""Tests for the agent chat loop (issue #6).

`agent_chat` talks to Ollama through a single `client.post` to `/api/chat`, so
the whole tool-use loop can be driven by scripting those responses -- no model,
no GPU, no network.

The loop's VRAM release is the reason this file exists. `run_workflow` does not
execute anything server-side; it returns a payload the UI acts on once the HTTP
response lands, so the chat model has to be released as the response goes out.
That release originally sat at the `for` statement's indentation, which meant it
only ran when all 10 rounds were exhausted -- every successful turn returns from
inside the loop and skipped it (fixed in 9b1e7d4, found by reading rather than
by a failing test). These tests pin both exit paths.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

import routers.agent as agent_module

OLLAMA_URL = "http://ollama.test:11434"
CHAT_URL = f"{OLLAMA_URL}/api/chat"
GENERATE_URL = f"{OLLAMA_URL}/api/generate"


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"fake response status {self.status_code}")


class _FakeOllama:
    """Scripts `/api/chat` replies and records every request the loop makes.

    One instance stands in for `httpx.AsyncClient` everywhere in the module,
    including the client `execute_tool` opens, so nothing escapes to the network.
    """

    def __init__(self, chat_replies: list[_FakeResponse]) -> None:
        self._chat_replies = list(chat_replies)
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.chat_call_count = 0

    # Used as a drop-in for `httpx.AsyncClient(...)`.
    def __call__(self, *args, **kwargs) -> "_FakeOllama":
        return self

    async def __aenter__(self) -> "_FakeOllama":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None, **kwargs) -> _FakeResponse:
        self.posts.append((url, json or {}))
        if url == CHAT_URL:
            self.chat_call_count += 1
            if not self._chat_replies:
                raise AssertionError(
                    f"loop asked for chat reply {self.chat_call_count}, "
                    "but the script ran out"
                )
            return self._chat_replies.pop(0)
        return _FakeResponse({})

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        self.gets.append(url)
        return _FakeResponse({})

    # -- assertions helpers -------------------------------------------------
    @property
    def release_calls(self) -> list[dict]:
        """Every `keep_alive: 0` release posted to /api/generate."""
        return [body for url, body in self.posts if url == GENERATE_URL]


def _assistant(content: str = "", **extra) -> _FakeResponse:
    return _FakeResponse({"message": {"role": "assistant", "content": content, **extra}})


def _tool_call(name: str, arguments: dict) -> _FakeResponse:
    return _FakeResponse({
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    })


def _run(request: agent_module.AgentChatRequest, fake: _FakeOllama):
    with patch.object(agent_module.httpx, "AsyncClient", fake):
        return asyncio.run(agent_module.agent_chat(request))


def _request(**overrides) -> agent_module.AgentChatRequest:
    fields = {
        "messages": [agent_module.ChatMessage(role="user", content="hello")],
        "ollama_url": OLLAMA_URL,
        "model": "test-model",
    }
    fields.update(overrides)
    return agent_module.AgentChatRequest(**fields)


WORKFLOW_CONTEXT = {"workflows": [{"id": "wf-1", "name": "Image to Mesh"}]}


class PlainTurnTests(unittest.TestCase):
    def test_a_turn_with_no_tool_calls_returns_the_assistant_message(self) -> None:
        fake = _FakeOllama([_assistant("Nothing to do here.")])

        response = _run(_request(), fake)

        self.assertEqual("Nothing to do here.", response.message)
        self.assertEqual([], response.actions)
        self.assertIsNone(response.thinking)
        self.assertEqual(1, fake.chat_call_count)

    def test_a_plain_turn_does_not_release_the_chat_model(self) -> None:
        # Negative control for the release tests below: without this, a release
        # assertion could be satisfied by code that releases unconditionally.
        fake = _FakeOllama([_assistant("Just answering.")])

        _run(_request(), fake)

        self.assertEqual([], fake.release_calls)


class VramReleaseTests(unittest.TestCase):
    def test_run_workflow_releases_vram_on_the_early_return_path(self) -> None:
        # Round 1 calls run_workflow, round 2 answers with no tool calls, which
        # returns from inside the loop. This is the path the original bug missed.
        fake = _FakeOllama([
            _tool_call("run_workflow", {"workflow_id": "wf-1"}),
            _assistant("Started it."),
        ])

        response = _run(_request(context=WORKFLOW_CONTEXT), fake)

        self.assertEqual("Started it.", response.message)
        self.assertEqual(["run_workflow"], [a.tool for a in response.actions])
        self.assertEqual(
            [{"model": "test-model", "keep_alive": 0}],
            fake.release_calls,
            "the chat model was not released on the early-return path",
        )

    def test_run_workflow_releases_vram_on_the_exhausted_loop_path(self) -> None:
        # Never stops calling tools, so the loop runs out its 10 rounds and
        # falls through to the bottom return instead of returning early.
        fake = _FakeOllama(
            [_tool_call("run_workflow", {"workflow_id": "wf-1"}) for _ in range(10)]
        )

        response = _run(_request(context=WORKFLOW_CONTEXT), fake)

        self.assertEqual("Reached maximum tool iterations.", response.message)
        self.assertEqual(10, fake.chat_call_count)
        self.assertEqual(
            [{"model": "test-model", "keep_alive": 0}],
            fake.release_calls,
            "the chat model was not released on the exhausted-loop path",
        )

    def test_a_tool_turn_without_run_workflow_does_not_release(self) -> None:
        # get_mesh_info is answered from context, so this stays offline too.
        fake = _FakeOllama([
            _tool_call("get_mesh_info", {}),
            _assistant("That mesh has 1,000 triangles."),
        ])

        response = _run(_request(context={"currentMeshPath": "a/b.glb"}), fake)

        self.assertEqual(["get_mesh_info"], [a.tool for a in response.actions])
        self.assertEqual(
            [],
            fake.release_calls,
            "released the chat model for a turn that never queued a workflow",
        )

    def test_a_failing_release_does_not_break_the_response(self) -> None:
        # The release is best-effort: Ollama may already be gone. The turn's
        # result must survive it.
        class _ExplodingRelease(_FakeOllama):
            async def post(self, url, json=None, **kwargs):
                if url == GENERATE_URL:
                    self.posts.append((url, json or {}))
                    raise httpx.ConnectError("ollama went away")
                return await super().post(url, json=json, **kwargs)

        fake = _ExplodingRelease([
            _tool_call("run_workflow", {"workflow_id": "wf-1"}),
            _assistant("Started it."),
        ])

        response = _run(_request(context=WORKFLOW_CONTEXT), fake)

        self.assertEqual("Started it.", response.message)
        self.assertEqual(1, len(fake.release_calls), "release was never attempted")


class OllamaErrorTests(unittest.TestCase):
    def test_a_non_200_returns_the_friendly_error_instead_of_raising(self) -> None:
        fake = _FakeOllama([_FakeResponse({}, status_code=500)])

        response = _run(_request(), fake)

        self.assertIn("Ollama error (500)", response.message)
        self.assertIn(OLLAMA_URL, response.message)
        self.assertEqual([], response.actions)


class ThinkingExtractionTests(unittest.TestCase):
    def test_native_thinking_field_is_surfaced(self) -> None:
        fake = _FakeOllama([_assistant("The answer.", thinking="pondering")])

        response = _run(_request(), fake)

        self.assertEqual("pondering", response.thinking)
        self.assertEqual("The answer.", response.message)

    def test_think_tags_are_stripped_from_content_and_surfaced(self) -> None:
        fake = _FakeOllama([_assistant("<think>step one</think>The answer.")])

        response = _run(_request(), fake)

        self.assertEqual("step one", response.thinking)
        self.assertEqual(
            "The answer.",
            response.message,
            "the <think> block was left in the user-visible message",
        )

    def test_thinking_from_several_rounds_is_combined(self) -> None:
        fake = _FakeOllama([
            _FakeResponse({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": "first",
                    "tool_calls": [{"function": {"name": "get_mesh_info", "arguments": {}}}],
                }
            }),
            _assistant("Done.", thinking="second"),
        ])

        response = _run(_request(context={"currentMeshPath": "a/b.glb"}), fake)

        self.assertEqual("first\n\n---\n\nsecond", response.thinking)

    def test_thinking_is_none_when_the_model_never_thinks(self) -> None:
        fake = _FakeOllama([_assistant("Plain answer.")])

        response = _run(_request(), fake)

        self.assertIsNone(response.thinking)


class ThinkingRequestParamTests(unittest.TestCase):
    def _chat_body(self, thinking: str) -> dict:
        fake = _FakeOllama([_assistant("ok")])
        _run(_request(thinking=thinking), fake)
        return next(body for url, body in fake.posts if url == CHAT_URL)

    def test_thinking_on_asks_ollama_to_think(self) -> None:
        self.assertIs(True, self._chat_body("on").get("think"))

    def test_thinking_off_asks_ollama_not_to_think(self) -> None:
        self.assertIs(False, self._chat_body("off").get("think"))

    def test_thinking_auto_leaves_the_choice_to_ollama(self) -> None:
        self.assertNotIn(
            "think",
            self._chat_body("auto"),
            "'auto' must not pin the think flag either way",
        )


if __name__ == "__main__":
    unittest.main()
