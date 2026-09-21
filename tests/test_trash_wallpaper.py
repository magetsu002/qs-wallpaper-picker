from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trash_wallpaper.sh"


class TrashWallpaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wallpapers = self.root / "wallpapers"
        self.state = self.root / "state" / "maho" / "wallpaper"
        self.bin = self.root / "bin"
        self.trash = self.root / "trash"
        for path in (self.wallpapers, self.state, self.bin, self.trash):
            path.mkdir(parents=True, exist_ok=True)
        gio = self.bin / "gio"
        gio.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "[[ $1 == trash ]]\nshift\n[[ ${1:-} == -- ]] && shift\n"
            'mv -- "$1" "$MAHO_TEST_TRASH/"\n',
            encoding="utf-8",
        )
        gio.chmod(0o755)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_script(self, name: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin}:{env['PATH']}",
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "MAHO_TEST_TRASH": str(self.trash),
        })
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.wallpapers), name],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_non_active_wallpaper_moves_to_recoverable_trash(self) -> None:
        target = self.wallpapers / "old image.png"
        target.write_bytes(b"not-current")
        result = self.run_script(target.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())
        self.assertTrue((self.trash / target.name).exists())

    def test_active_wallpaper_is_preserved(self) -> None:
        target = self.wallpapers / "current.png"
        target.write_bytes(b"current")
        (self.state / "current.json").write_text(
            json.dumps({"path": str(target)}), encoding="utf-8"
        )
        result = self.run_script(target.name)
        self.assertEqual(result.returncode, 3)
        self.assertTrue(target.exists())

    def test_path_traversal_is_rejected(self) -> None:
        result = self.run_script("../outside.png")
        self.assertEqual(result.returncode, 2)

    def test_picker_exposes_hover_only_recoverable_delete(self) -> None:
        qml = (ROOT / "WallpaperPicker.qml").read_text(encoding="utf-8")
        self.assertIn("visible: wallpaperHover.hovered && !window.isOnlineSearch", qml)
        self.assertIn('window.resolveProjectScript("scripts/trash_wallpaper.sh")', qml)
        self.assertIn("mouse.accepted = true", qml)
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('gio trash -- "$TARGET"', script)
        self.assertNotIn("rm -f -- \"$TARGET\"", script)


if __name__ == "__main__":
    unittest.main()
