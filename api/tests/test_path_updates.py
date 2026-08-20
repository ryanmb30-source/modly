"""Regression tests for issue #5 -- a runtime path change must reach every consumer.

`POST /settings/paths` calls `generator_registry.update_paths()`, which rebinds
`MODELS_DIR` / `WORKSPACE_DIR` as attributes on `services.generator_registry`.

A module that does `from services.generator_registry import WORKSPACE_DIR` binds
the *value* at import time. Rebinding the registry attribute afterwards does not
touch that module's own name, so it keeps serving the old root forever. Modules
that go through the module object (`reg_module.WORKSPACE_DIR`) resolve live and
follow the change.

The two styles were mixed, including *within a single file*: `optimize.py`
resolved its input against the live root but decided its output directory
against the stale one.
"""

import ast
import importlib
import tempfile
import unittest
from pathlib import Path

import trimesh

import services.generator_registry as registry_module

API_DIR = Path(__file__).resolve().parent.parent

# `routers.optimize` pulls in pymeshlab, which costs ~12s to import. At module
# scope that is paid during discovery, i.e. on every run of every other test
# module too. Imported inside the tests that need it, only those tests pay.
CONSUMER_MODULES = ("routers.generation", "routers.model", "routers.optimize")

# Module-level path state on the registry. MODELS_DIR and WORKSPACE_DIR are
# rebound at runtime by `update_paths()`. EXTENSIONS_DIR is not, today -- it is
# held to the same rule so that the guard below stays a single blanket rule
# rather than a per-name exception list that the next reader has to trust.
MUTABLE_PATH_CONSTANTS = {"MODELS_DIR", "WORKSPACE_DIR", "EXTENSIONS_DIR"}


class PathConstantImportStyleTests(unittest.TestCase):
    """Static guard: nothing may bind these constants by value.

    The behavioural tests below cover the consumers that exist today. This one
    keeps the *next* consumer from reintroducing the bug, which is the failure
    mode the issue actually describes -- it had already been fixed at one call
    site without being fixed as a pattern.
    """

    def test_no_module_imports_a_mutable_path_constant_by_value(self) -> None:
        offenders: list[str] = []

        for source_file in sorted(API_DIR.rglob("*.py")):
            relative = source_file.relative_to(API_DIR)
            if any(part in {".venv", "__pycache__"} for part in relative.parts):
                continue

            tree = ast.parse(source_file.read_text(encoding="utf-8"), str(source_file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if not (node.module or "").endswith("generator_registry"):
                    continue
                for alias in node.names:
                    if alias.name in MUTABLE_PATH_CONSTANTS:
                        offenders.append(
                            f"{relative.as_posix()}:{node.lineno} "
                            f"imports {alias.name} by value"
                        )

        self.assertEqual(
            [],
            offenders,
            "These bind a path constant at import time and will not see a "
            "runtime path change. Read it through the module instead "
            "(`import services.generator_registry as reg_module`, then "
            "`reg_module.WORKSPACE_DIR`):\n  " + "\n  ".join(offenders),
        )


class _WorkspaceSwap(unittest.TestCase):
    """Points the registry at a scratch workspace, restoring the real one after.

    Mirrors what `update_paths()` does to the module, without `unload_all()`
    touching the live registry's generators.
    """

    def setUp(self) -> None:
        self._real_workspace = registry_module.WORKSPACE_DIR
        self._real_models = registry_module.MODELS_DIR
        self.root = Path(tempfile.mkdtemp())
        self.new_workspace = self.root / "new-workspace"
        self.new_models = self.root / "new-models"
        self.new_workspace.mkdir()
        self.new_models.mkdir()

    def tearDown(self) -> None:
        registry_module.WORKSPACE_DIR = self._real_workspace
        registry_module.MODELS_DIR = self._real_models


class ConsumerModulesHoldNoOwnBindingTests(unittest.TestCase):
    """No consumer module may own a name for a constant the registry rebinds.

    Asserted as absence rather than as "the copy agrees": a module-level copy
    agrees right up until the moment `update_paths()` runs, so a value check
    would pass on exactly the code this issue is about.
    """

    def test_consumer_modules_define_no_path_constant_of_their_own(self) -> None:
        consumers = {name: importlib.import_module(name) for name in CONSUMER_MODULES}
        owned = [
            f"{name}.{constant}"
            for name, module in consumers.items()
            for constant in sorted(MUTABLE_PATH_CONSTANTS)
            if hasattr(module, constant)
        ]
        self.assertEqual(
            [],
            owned,
            "These modules hold their own binding, which stops tracking the "
            "registry the moment update_paths() runs:\n  " + "\n  ".join(owned),
        )


class TransformMeshHonoursNewWorkspaceTests(_WorkspaceSwap):
    """End-to-end: the failure a user actually hits after changing the workspace.

    `transform_mesh` resolves its input against the live root, then decides its
    output directory and builds the response URL against WORKSPACE_DIR. With a
    stale copy the input resolves inside the new root but fails the containment
    test against the old one, so the result is written to `OLD_ROOT/Workflows`.
    Nothing raises: `relative_to(OLD_ROOT)` succeeds, so the response is a
    plausible-looking `/workspace/Workflows/<name>.glb`. That URL is served from
    the *new* root, where the file does not exist, so the caller gets a 404 for
    a transform that reported success -- and the mesh is sitting outside the
    configured workspace.
    """

    def tearDown(self) -> None:
        # Pre-fix, the code under test writes into the real workspace. Do not
        # leave that behind for the user (or for the next run) to trip over.
        for stray in self._real_workspace.rglob("cube_xf_*.glb"):
            stray.unlink(missing_ok=True)
        super().tearDown()

    def test_output_lands_in_the_new_workspace(self) -> None:
        optimize_module = importlib.import_module("routers.optimize")

        collection = "Session"
        (self.new_workspace / collection).mkdir()
        source = self.new_workspace / collection / "cube.glb"
        trimesh.creation.box((1.0, 1.0, 1.0)).export(str(source))

        # Go through the real entry point that POST /settings/paths uses.
        # models_dir is None so unload_all() does not touch live generators.
        registry_module.generator_registry.update_paths(None, self.new_workspace)
        self.assertEqual(self.new_workspace, registry_module.WORKSPACE_DIR)

        identity = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        body = optimize_module.TransformRequest(
            path=f"{collection}/cube.glb", matrix=identity
        )

        result = optimize_module.transform_mesh(body)

        url = result["url"]
        self.assertTrue(
            url.startswith(f"/workspace/{collection}/"),
            f"expected a URL under the new workspace collection, got {url!r}",
        )
        written = self.new_workspace / url[len("/workspace/"):]
        self.assertTrue(
            written.is_file(),
            f"transform output was not written into the new workspace: {written}",
        )
        leaked = sorted(
            str(p) for p in self._real_workspace.rglob("cube_xf_*.glb")
        )
        self.assertEqual(
            [],
            leaked,
            "transform output leaked into the previously configured workspace",
        )


if __name__ == "__main__":
    unittest.main()
