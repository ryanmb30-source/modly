"""Tests for the shared-secret API gate (issue #9).

Driven through a real FastAPI app with the real middleware, so the tests
exercise the same ordering and exemption logic production uses rather than
calling the predicate directly.
"""

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api_auth import (
    ENV_ALLOW_UNAUTHENTICATED,
    ENV_TOKEN,
    TOKEN_HEADER,
    api_token_middleware,
    redact_token,
)

TOKEN = "0123456789abcdef" * 4


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(api_token_middleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/settings/paths")
    async def paths():
        return {"workspace_dir": "C:/Users/someone/workspace"}

    @app.post("/extensions/setup/{ext_id}")
    async def setup(ext_id: str):
        return {"ran": ext_id}

    return app


def _env(**overrides) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in {ENV_TOKEN, ENV_ALLOW_UNAUTHENTICATED}}
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


class TokenRequiredTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_app())

    def test_a_request_with_no_token_is_rejected(self) -> None:
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/settings/paths")
        self.assertEqual(401, response.status_code)

    def test_the_right_token_in_the_header_is_accepted(self) -> None:
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/settings/paths", headers={TOKEN_HEADER: TOKEN})
        self.assertEqual(200, response.status_code)

    def test_the_right_token_as_a_query_param_is_accepted(self) -> None:
        # Asset URLs go to three.js loaders, which cannot set headers.
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get(f"/settings/paths?modly_token={TOKEN}")
        self.assertEqual(200, response.status_code)

    def test_a_wrong_token_is_rejected(self) -> None:
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/settings/paths", headers={TOKEN_HEADER: "wrong"})
        self.assertEqual(401, response.status_code)

    def test_a_token_that_is_a_prefix_of_the_real_one_is_rejected(self) -> None:
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/settings/paths", headers={TOKEN_HEADER: TOKEN[:-1]})
        self.assertEqual(401, response.status_code)

    def test_the_setup_endpoint_that_runs_code_is_gated(self) -> None:
        # The reason this issue outranked the others: POST /extensions/setup
        # executes the extension's setup.py, and a cross-origin POST is a simple
        # request, so CORS never stopped it from being sent.
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            unauthenticated = self.client.post("/extensions/setup/anything")
            authenticated = self.client.post(
                "/extensions/setup/anything", headers={TOKEN_HEADER: TOKEN}
            )
        self.assertEqual(401, unauthenticated.status_code)
        self.assertEqual(200, authenticated.status_code)

    def test_a_huggingface_token_query_param_does_not_authenticate(self) -> None:
        # /model/hf-download already takes `token` for the HuggingFace token, so
        # the API token uses `modly_token`. If the two shared a name, an HF token
        # would be read as an API token: every gated download would 401, and a
        # value from a different trust domain would be compared against ours.
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/settings/paths?token=some-huggingface-token")
        self.assertEqual(401, response.status_code)

    def test_health_stays_reachable_so_startup_can_be_diagnosed(self) -> None:
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            response = self.client.get("/health")
        self.assertEqual(200, response.status_code)


class MisconfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_app())

    def test_an_unset_token_refuses_every_request_rather_than_failing_open(self) -> None:
        # If a packaging bug dropped the env var, failing open would silently
        # disable authentication and nothing would look wrong.
        with patch.dict(os.environ, _env(), clear=True):
            response = self.client.get("/settings/paths")
        self.assertEqual(503, response.status_code)
        self.assertIn(ENV_TOKEN, response.json()["detail"])

    def test_the_dev_escape_hatch_must_be_set_explicitly(self) -> None:
        with patch.dict(os.environ, _env(MODLY_ALLOW_UNAUTHENTICATED="1"), clear=True):
            response = self.client.get("/settings/paths")
        self.assertEqual(200, response.status_code)

    def test_an_unrelated_value_does_not_enable_the_escape_hatch(self) -> None:
        with patch.dict(os.environ, _env(MODLY_ALLOW_UNAUTHENTICATED="0"), clear=True):
            response = self.client.get("/settings/paths")
        self.assertEqual(503, response.status_code)

    def test_a_configured_token_wins_over_the_escape_hatch(self) -> None:
        with patch.dict(
            os.environ,
            _env(MODLY_API_TOKEN=TOKEN, MODLY_ALLOW_UNAUTHENTICATED="1"),
            clear=True,
        ):
            response = self.client.get("/settings/paths")
        self.assertEqual(
            401,
            response.status_code,
            "the escape hatch must not weaken a properly configured backend",
        )


class TokenIsReadLiveTests(unittest.TestCase):
    """The token must not be captured at import time (the WORKSPACE_DIR lesson)."""

    def test_changing_the_env_changes_the_expected_token(self) -> None:
        client = TestClient(_build_app())
        with patch.dict(os.environ, _env(MODLY_API_TOKEN="first-token"), clear=True):
            self.assertEqual(
                200, client.get("/health").status_code
            )
            self.assertEqual(
                200,
                client.get("/settings/paths", headers={TOKEN_HEADER: "first-token"}).status_code,
            )
        with patch.dict(os.environ, _env(MODLY_API_TOKEN="second-token"), clear=True):
            self.assertEqual(
                401,
                client.get("/settings/paths", headers={TOKEN_HEADER: "first-token"}).status_code,
                "a stale token was still being accepted",
            )
            self.assertEqual(
                200,
                client.get("/settings/paths", headers={TOKEN_HEADER: "second-token"}).status_code,
            )


class RedactionTests(unittest.TestCase):
    def test_the_token_is_blanked_out_of_log_lines(self) -> None:
        line = f'GET /workspace/a.glb?modly_token={TOKEN} HTTP/1.1" 200'
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            redacted = redact_token(line)
        self.assertNotIn(TOKEN, redacted)
        self.assertIn("modly_token=REDACTED", redacted)

    def test_redaction_leaves_unrelated_lines_alone(self) -> None:
        line = 'GET /model/all HTTP/1.1" 200'
        with patch.dict(os.environ, _env(MODLY_API_TOKEN=TOKEN), clear=True):
            self.assertEqual(line, redact_token(line))

    def test_redaction_is_a_no_op_when_no_token_is_configured(self) -> None:
        with patch.dict(os.environ, _env(), clear=True):
            self.assertEqual("anything", redact_token("anything"))


if __name__ == "__main__":
    unittest.main()
