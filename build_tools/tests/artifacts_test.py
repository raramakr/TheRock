from pathlib import Path
import os
import tempfile
import unittest
import sys

sys.path.insert(0, os.fspath(Path(__file__).parent.parent))

from _therock_utils.artifacts import ArtifactName


class ArtifactNameTest(unittest.TestCase):
    def setUp(self):
        override_temp = os.getenv("TEST_TMPDIR")
        if override_temp is not None:
            self.temp_context = None
            self.temp_dir = Path(override_temp)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.temp_context = tempfile.TemporaryDirectory()
            self.temp_dir = Path(self.temp_context.name)

    def tearDown(self):
        if self.temp_context:
            self.temp_context.cleanup()

    def testFromPath(self):
        p1 = Path(self.temp_dir / "dir" / "name_component_generic")
        p1.mkdir(parents=True, exist_ok=True)
        p2 = Path(self.temp_dir / "other" / "name_component_generic.tar.xz")
        p2.parent.mkdir(parents=True, exist_ok=True)
        p2.touch()

        an1 = ArtifactName.from_path(p1)
        an2 = ArtifactName.from_path(p2)
        self.assertEqual(an1.name, "name")
        self.assertEqual(an1.component, "component")
        self.assertEqual(an2.target_family, "generic")
        self.assertEqual(an1, an2)
        self.assertEqual(hash(an1), hash(an2))

    def testFromFilename(self):
        f1 = "name_component_generic.tar.xz"
        an1 = ArtifactName.from_filename(f1)
        self.assertEqual(an1.name, "name")
        self.assertEqual(an1.component, "component")
        self.assertEqual(an1.target_family, "generic")

        f_invalid1 = "invalid_name.zip"
        an_invalid1 = ArtifactName.from_filename(f_invalid1)
        self.assertIsNone(an_invalid1)

        # Component names containing one underscore could be misinterpretted
        # as [name]_[component]_[target family]_suffix with each group shifted
        # over one. See https://github.com/ROCm/TheRock/issues/935.
        f_invalid2 = "underscore_name_component_generic.tar.xz"
        an_invalid2 = ArtifactName.from_filename(f_invalid2)
        self.assertIsNone(an_invalid2)


if __name__ == "__main__":
    unittest.main()
