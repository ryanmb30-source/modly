"""Export source resolution for both mesh routes (issue #10).

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
        resolved = self.optimize._resolve_exportable_path(str(self.registered))
        self.assertEqual(self.registered.resolve(), resolved)

    def test_a_file_that_exists_but_was_never_opened_is_refused(self) -> None:
        # The negative control. If this ever passes, the allowlist has been
        # widened into "any absolute path", which is arbitrary file read.
        with self.assertRaises(HTTPException) as caught:
            self.optimize._resolve_exportable_path(str(self.never_opened))
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
            resolved = self.optimize._resolve_exportable_path("Session/cube.glb")
            self.assertEqual(generated.resolve(), resolved)

            # Containment is unchanged for the relative branch.
            with self.assertRaises(HTTPException) as caught:
                self.optimize._resolve_exportable_path("../../../Windows/win.ini")
            self.assertEqual(400, caught.exception.status_code)
        finally:
            registry_module.WORKSPACE_DIR = previous

    def test_registration_is_matched_on_the_resolved_path(self) -> None:
        # An un-normalised spelling of the same registered file must still be
        # accepted, or the viewer's own URLs would intermittently fail.
        awkward = self.root / "." / "opened-by-the-user.glb"
        resolved = self.optimize._resolve_exportable_path(str(awkward))
        self.assertEqual(self.registered.resolve(), resolved)


if __name__ == "__main__":
    unittest.main()
