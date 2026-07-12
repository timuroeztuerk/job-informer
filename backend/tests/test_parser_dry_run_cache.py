"""Parser dry-run and cache regression tests."""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from backend.src.agents.parser import DescriptionTools, JobDescriptionStructure
from backend.src.utils.database import JobDatabase
from backend.src.utils.openai_responses_client import (
    LLMCallTelemetry,
    LLMUsage,
    StructuredLLMResult,
)


def _config(*, dry_run: bool) -> SimpleNamespace:
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_model="gpt-5-mini",
        desc_parser_model="gpt-5-mini",
        desc_parser_prompt="",
        desc_parser_version=1,
        desc_parser_min_chars=10,
        desc_parser_max_chars=12_000,
        desc_parser_dry_run=dry_run,
        desc_parser_concurrency=1,
        desc_parser_batch_size=25,
        desc_parser_max_batches=1,
        llm_timeout_seconds=1.0,
        llm_max_retries=1,
        llm_retry_base_delay=0.0,
        llm_retry_max_delay=0.0,
        llm_retry_jitter=0.0,
    )


def _successful_result(summary: str) -> StructuredLLMResult[JobDescriptionStructure]:
    return StructuredLLMResult(
        parsed=JobDescriptionStructure.model_validate({"summary": summary}),
        telemetry=LLMCallTelemetry(
            model="gpt-5-mini",
            status="success",
            started_at="2026-07-12T10:00:00+00:00",
            finished_at="2026-07-12T10:00:01+00:00",
            latency_ms=100.0,
            response_id="resp_parser_cache_test",
            output_preview=json.dumps({"summary": summary}),
            usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        ),
    )


class _FakeParserClient:
    api_key = "test-key"
    model = "gpt-5-mini"

    def __init__(self, *results: StructuredLLMResult[JobDescriptionStructure]) -> None:
        self.results = list(results)
        self.calls = 0

    async def parse_structured_async(self, **_kwargs):
        self.calls += 1
        if not self.results:
            raise AssertionError("Unexpected parser LLM call")
        return self.results.pop(0)


class TestParserDryRunCache(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs_df = pd.DataFrame(
            [
                {
                    "job_id": "linkedin:dry-run-cache-test",
                    "title": "Data Scientist",
                    "company": "ACME",
                    "description": "Build reliable machine learning systems for analytics teams.",
                }
            ]
        )

    def test_dry_run_is_read_only_and_does_not_block_real_parse(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=f"{tmp_dir}/jobs.db")
            config = _config(dry_run=True)
            client = _FakeParserClient(_successful_result("Canonical real parse."))
            parser = DescriptionTools(config=config, db=db, llm_client=client)

            self.assertEqual(parser.parse_batch(self.jobs_df), 0)
            self.assertEqual(client.calls, 0)

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parsed_descriptions").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parse_job_states").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM llm_attempts").fetchone()[0], 0)

            config.desc_parser_dry_run = False
            self.assertEqual(parser.parse_batch(self.jobs_df), 1)
            self.assertEqual(parser.parse_batch(self.jobs_df), 0)
            self.assertEqual(client.calls, 1)

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                payload_json = conn.execute(
                    "SELECT payload_json FROM parsed_descriptions"
                ).fetchone()[0]
            self.assertEqual(json.loads(payload_json)["summary"], "Canonical real parse.")

    def test_legacy_placeholder_is_reparsed_and_atomically_superseded(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=f"{tmp_dir}/jobs.db")
            client = _FakeParserClient(_successful_result("Replacement parse."))
            parser = DescriptionTools(config=_config(dry_run=False), db=db, llm_client=client)
            description = parser._prepare_description_for_llm(  # noqa: SLF001
                self.jobs_df.iloc[0]["description"]
            )
            desc_hash = parser._hash_description(description)  # noqa: SLF001

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                conn.execute(
                    """
                    INSERT INTO parsed_descriptions (
                        job_id, desc_hash, version, model, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "linkedin:dry-run-cache-test",
                        desc_hash,
                        1,
                        "legacy-model",
                        ' { "dry_run" : true } ',
                        "2026-04-01T10:00:00+00:00",
                    ),
                )
                conn.commit()
            db.upsert_parse_job_state(
                job_id="linkedin:dry-run-cache-test",
                desc_hash=desc_hash,
                version=1,
                model="legacy-model",
                status="success",
                last_attempt_at="2026-04-01T10:00:00+00:00",
                last_success_at="2026-04-01T10:00:00+00:00",
                output_preview='{"dry_run": true}',
            )

            self.assertEqual(
                parser.parse_jobs_dataframe(self.jobs_df, batch_size=1, max_batches=1),
                1,
            )
            self.assertEqual(
                parser.parse_jobs_dataframe(self.jobs_df, batch_size=1, max_batches=1),
                0,
            )
            self.assertEqual(client.calls, 1)

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                rows = conn.execute(
                    "SELECT model, payload_json FROM parsed_descriptions"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "gpt-5-mini")
            self.assertEqual(json.loads(rows[0][1])["summary"], "Replacement parse.")

            status = db.get_parse_status("linkedin:dry-run-cache-test")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status["status"], "success")
            self.assertIn("Replacement parse.", status["output_preview"])


if __name__ == "__main__":
    unittest.main()
