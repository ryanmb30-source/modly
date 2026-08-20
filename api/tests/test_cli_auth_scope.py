"""The CLI must send Modly's token to Modly and nowhere else (issue #9).

`tools/modly-cli/agent.py` talks to two unrelated services over the same helper:
the Modly backend and a ComfyUI instance. Attaching the token to every request
would hand Modly's credential to ComfyUI, so the header is scoped by origin.

That is a silent failure if it regresses -- nothing breaks, the token just goes
somewhere it should not -- which is why it is pinned here rather than left to
the CLI's own behaviour.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

CLI_PATH = Path(__file__).resolve().parent.parent.parent / "tools" / "modly-cli" / "agent.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("modly_cli_agent_under_test", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(CLI_PATH.is_file(), f"CLI not present at {CLI_PATH}")
class CliTokenScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_cli()

    def test_the_token_is_sent_to_the_modly_backend(self) -> None:
        headers = self.cli._auth_headers_for("http://127.0.0.1:8765/model/all")
        self.assertEqual(self.cli.MODLY_API_TOKEN, headers.get(self.cli.TOKEN_HEADER))

    def test_the_token_is_not_sent_to_comfyui(self) -> None:
        headers = self.cli._auth_headers_for("http://127.0.0.1:8188/prompt")
        self.assertEqual({}, headers, "Modly's token must not reach ComfyUI")

    def test_the_token_is_not_sent_to_an_arbitrary_host(self) -> None:
        self.assertEqual({}, self.cli._auth_headers_for("https://example.com/anything"))

    def test_a_registered_origin_covers_only_that_host_and_port(self) -> None:
        self.cli._register_modly_origin("http://127.0.0.1:9999")
        self.assertNotEqual({}, self.cli._auth_headers_for("http://127.0.0.1:9999/health"))
        self.assertEqual(
            {},
            self.cli._auth_headers_for("http://127.0.0.1:9998/health"),
            "registering one port must not authorise a neighbouring one",
        )

    def test_a_spawned_backend_receives_a_token(self) -> None:
        # A backend started without one refuses every request, so the CLI
        # would be unusable in its own-backend mode.
        self.assertTrue(self.cli.MODLY_API_TOKEN)
        self.assertGreaterEqual(len(self.cli.MODLY_API_TOKEN), 32)


if __name__ == "__main__":
    unittest.main()
