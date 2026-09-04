"""Tests for mounting the bundled frontend without an HTTP client dependency."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.api import mount_frontend


class TestFrontendServing(unittest.TestCase):
    def test_frontend_mount_has_assets_and_index_only_fallback(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir) / "dist"
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True)
            (dist_dir / "index.html").write_text("SPA shell", encoding="utf-8")

            app = FastAPI()
            mount_frontend(app, dist_dir)

            paths = [route.path for route in app.routes]
            self.assertIn("/assets", paths)
            fallback = next(route for route in app.routes if route.path == "/{full_path:path}")
            response = asyncio.run(fallback.endpoint("../secret.txt"))
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), dist_dir / "index.html")


if __name__ == "__main__":
    unittest.main()
