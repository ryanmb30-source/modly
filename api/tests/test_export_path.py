"""Mesh source resolution for the optimize and export endpoints (issues #4, #10).

A mesh reaches the viewer either by being generated into the workspace or by
being imported from an arbitrary location on disk. `/optimize/export` only
handled the first, so exporting an imported mesh to obj/stl/ply died with
400 Invalid path.

The absolute-path branch added for the second case is authorised by membership
of `_SERVABLE_PATHS`, the same allowlist `serve_file` uses. The tests below pin
that it is genuinely an allowlist -- a file that merely exists is refused --
because widening this into "any absolute path that exists" would re-open the
arbitrary-read hole closed in issue #3.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException


class ExportPathResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Imported here rather than at module scope: routers.optimize pulls in
        # pymeshlab, which costs ~12s and would be paid during test discovery.
        import routers.optimize as optimize_module

        cls.optimize = optimize_module

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.registered = self.root / "opened-by-the-user.glb"
        self.registered.write_bytes(b"glb")
        self.never_opened = self.root / "never-opened.glb"
        self.never_opened.write_bytes(b"glb")

        self._saved_servable = set(self.optimize._SERVABLE_PATHS)
        self.optimize._register_servable(self.registered)

    def tearDown(self) -> None:
        self.optimize._SERVABLE_PATHS.clear()
        self.optimize._SERVABLE_PATHS.update(self._saved_servable)

    def test_an_imported_mesh_resolves_to_its_path_on_disk(self) -> None:
        resolved = self.optimize._resolve_mesh_source(str(self.registered))
        self.assertEqual(self.registered.resolve(), resolved)

    def test_a_file_that_exists_but_was_never_opened_is_refused(self) -> None:
        # The negative control. If this ever passes, the allowlist has been
        # widened into "any absolute path", which is arbitrary file read.
        with self.assertRaises(HTTPException) as caught:
            self.optimize._resolve_mesh_source(str(self.never_opened))
        self.assertEqual(403, caught.exception.status_code)

    def test_a_workspace_relative_path_still_goes_through_containment(self) -> None:
        import services.generator_registry as registry_module

        previous = registry_module.WORKSPACE_DIR
        workspace = self.root / "workspace"
        (workspace / "Session").mkdir(parents=True)
        generated = workspace / "Session" / "cube.glb"
        generated.write_bytes(b"glb")
        registry_module.WORKSPACE_DIR = workspace
        try:
            resolved = self.optimize._resolve_mesh_source("Session/cube.glb")
            self.assertEqual(generated.resolve(), resolved)

            # Containment is unchanged for the relative branch.
            with self.assertRaises(HTTPException) as caught:
                self.optimize._resolve_mesh_source("../../../Windows/win.ini")
            self.assertEqual(400, caught.exception.status_code)
        finally:
            registry_module.WORKSPACE_DIR = previous

    def test_registration_is_matched_on_the_resolved_path(self) -> None:
        # An un-normalised spelling of the same registered file must still be
        # accepted, or the viewer's own URLs would intermittently fail.
        awkward = self.root / "." / "opened-by-the-user.glb"
        resolved = self.optimize._resolve_mesh_source(str(awkward))
        self.assertEqual(self.registered.resolve(), resolved)


class MeshOutputDirTests(unittest.TestCase):
    """Where a derived mesh is written (issue #4's bypass, missed in optimize.py).

    Tested separately from _resolve_mesh_source because the two guards mask each
    other: the allowlist refuses an unregistered path before the output rule is
    ever reached, so containment has to be exercised with a path that passes the
    allowlist and still lies outside the workspace.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import routers.optimize as optimize_module

        cls.optimize = optimize_module

    def setUp(self) -> None:
        import services.generator_registry as registry_module

        self.registry = registry_module
        self.previous_workspace = registry_module.WORKSPACE_DIR
        self.root = Path(tempfile.mkdtemp())
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        registry_module.WORKSPACE_DIR = self.workspace

    def tearDown(self) -> None:
        self.registry.WORKSPACE_DIR = self.previous_workspace

    def test_a_source_inside_the_workspace_writes_beside_itself(self) -> None:
        source = self.workspace / "Session" / "cube.glb"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"glb")

        output_dir, workspace = self.optimize._mesh_output_dir(source.resolve())
        self.assertEqual(source.parent.resolve(), output_dir)
        self.assertEqual(self.workspace.resolve(), workspace)

    def test_a_sibling_sharing_the_workspace_name_prefix_is_outside(self) -> None:
        # The #4 bypass: str.startswith accepted "<workspace>-evil" as inside,
        # so output was written there -- outside the workspace -- and only then
        # did relative_to() raise, turning an escape into a 500.
        sibling = self.root / "workspace-evil"
        sibling.mkdir()
        source = (sibling / "planted.glb").resolve()

        output_dir, workspace = self.optimize._mesh_output_dir(source)
        self.assertEqual(
            workspace / "Workflows",
            output_dir,
            "a sibling directory sharing the name prefix was treated as inside",
        )

    def test_every_output_path_stays_relative_to_the_returned_root(self) -> None:
        # The property each endpoint depends on: relative_to() must not raise,
        # which is what turned the escape into a 500 rather than a clean answer.
        sources = [
            (self.workspace / "Session" / "cube.glb"),
            (self.root / "workspace-evil" / "planted.glb"),
            (self.root / "somewhere-else" / "mesh.glb"),
        ]
        for source in sources:
            output_dir, workspace = self.optimize._mesh_output_dir(source.resolve())
            (output_dir / "out.glb").relative_to(workspace)


if __name__ == "__main__":
    unittest.main()
