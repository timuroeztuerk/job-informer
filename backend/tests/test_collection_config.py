"""Default collection-scope tests."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.src.config.settings import Config


class TestCollectionConfig(unittest.TestCase):
    def test_default_scope_covers_countries_and_retained_city_searches(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.touch()

            with patch.dict(os.environ, {}, clear=True):
                config = Config.from_env(str(env_path))

        self.assertEqual(config.get_keywords_list(), ["Data Scientist", "Data Analyst"])
        self.assertEqual(
            config.get_locations_list(),
            ["Germany", "Switzerland", "Berlin", "Stuttgart", "Frankfurt", "München"],
        )
        self.assertEqual(
            len(config.get_keywords_list()) * len(config.get_locations_list()),
            12,
        )


if __name__ == "__main__":
    unittest.main()
