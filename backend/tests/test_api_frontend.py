"""Tests for serving the bundled frontend safely."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import mount_frontend


class TestFrontendServing(unittest.TestCase):
    def test_spa_fallback_does_not_serve_encoded_parent_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            dist_dir = root / "dist"
            dist_dir.mkdir()
            (dist_dir / "index.html").write_text("SPA shell", encoding="utf-8")
            (root / "secret.txt").write_text("sensitive data", encoding="utf-8")

            test_app = FastAPI()
            mount_frontend(test_app, dist_dir)

            with TestClient(test_app) as client:
                response = client.get("/%2e%2e/secret.txt")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, "SPA shell")
            self.assertNotIn("sensitive data", response.text)

    def test_assets_are_served_from_the_assets_mount(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir) / "dist"
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True)
            (dist_dir / "index.html").write_text("SPA shell", encoding="utf-8")
            (assets_dir / "app.js").write_text("asset content", encoding="utf-8")

            test_app = FastAPI()
            mount_frontend(test_app, dist_dir)

            with TestClient(test_app) as client:
                asset_response = client.get("/assets/app.js")
                route_response = client.get("/jobs/linkedin%3A123")

            self.assertEqual(asset_response.status_code, 200)
            self.assertEqual(asset_response.text, "asset content")
            self.assertEqual(route_response.status_code, 200)
            self.assertEqual(route_response.text, "SPA shell")


if __name__ == "__main__":
    unittest.main()
