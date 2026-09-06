"""Publish reviewed saved drafts locally, without an AI client or new attempts."""

import argparse
import json
from pathlib import Path

from .evidence import EVIDENCE_VERSION
from .models import JobExtraction, validate_evidence
from .prompt import digest, encode
from .store import AIStore
from ..utils.time_utils import utc_now_iso


def _at_path(document, path):
    value = document
    for part in path:
        value = value[part]
    return value


def repair_saved_outputs(db_path, plan, *, apply=False):
    """Validate the complete plan first; commit all results in one transaction.

    Original response drafts, attempts, usage and source bytes stay immutable.
    Reviewed edits can only replace evidence or remove a specifically identified
    unsupported description-language claim. All other claims remain unchanged.
    """
    store = AIStore(db_path)
    ids = [item["work_id"] for item in plan["jobs"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate work IDs in repair plan.")
    results = []
    now = utc_now_iso()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        if apply:
            paused = conn.execute("SELECT paused FROM ai_queue_state WHERE id=1").fetchone()[0]
            running = conn.execute("SELECT 1 FROM ai_work WHERE status='running' LIMIT 1").fetchone()
            if not paused or running:
                raise ValueError("Pause AI extraction and let running requests finish before local repair.")
        pending = []
        for item in plan["jobs"]:
            work = conn.execute("SELECT * FROM ai_work WHERE work_id=?", (item["work_id"],)).fetchone()
            if work is None or not store.is_active(conn, work["job_id"]):
                raise ValueError(f"Work {item['work_id']} does not belong to an active job.")
            latest = conn.execute("SELECT MAX(work_id) FROM ai_work WHERE job_id=?", (work["job_id"],)).fetchone()[0]
            attempt = conn.execute("SELECT * FROM ai_attempts WHERE work_id=? ORDER BY attempt_id DESC LIMIT 1", (work["work_id"],)).fetchone()
            if latest != work["work_id"] or not attempt or attempt["attempt_id"] != item["attempt_id"]:
                raise ValueError(f"Work {item['work_id']} changed since review.")
            draft = json.loads(attempt["output_json"] or "null")
            if draft is None or digest(draft) != item["output_sha256"]:
                raise ValueError(f"Draft {item['work_id']} changed since review.")
            plan_hash = digest(item)
            if work["status"] == "succeeded" and work["extraction_id"]:
                saved = json.loads(conn.execute("SELECT data_json FROM description_extractions WHERE extraction_id=?", (work["extraction_id"],)).fetchone()[0])
                if saved.get("metadata", {}).get("local_repair", {}).get("plan_sha256") == plan_hash:
                    results.append({"work_id": work["work_id"], "job_id": work["job_id"], "status": "already_repaired"})
                    continue
            if work["status"] != "failed" or work["error_kind"] != "output":
                raise ValueError(f"Work {item['work_id']} is not a failed output.")
            sources = json.loads(work["input_json"])["sources"]
            reviewed = []
            for correction in item.get("corrections", []):
                evidence = _at_path(draft, correction["path"])
                if not isinstance(evidence, dict) or set(evidence) != {"source_ref", "quote"}:
                    raise ValueError("A reviewed correction must target one original evidence item.")
                if evidence != correction["original"]:
                    raise ValueError("Reviewed evidence no longer matches the draft.")
                replacement = correction["replacement"]
                if set(replacement) != {"source_ref", "quote"} or not replacement["quote"]:
                    raise ValueError("A correction must provide a source and nonempty original quotation.")
                source = sources.get(replacement["source_ref"])
                if source is None or replacement["quote"] not in source:
                    raise ValueError("Reviewed replacement is not an exact original source span.")
                evidence.update(replacement)
                reviewed.append(correction)
            removed = []
            for removal in item.get("remove_description_languages", []):
                language = removal["original"]
                if not removal.get("reason") or language not in draft["description_languages"]:
                    raise ValueError("A language removal must identify the original claim and its reason.")
                draft["description_languages"].remove(language)
                removed.append(removal)
            payload = validate_evidence(JobExtraction.model_validate(draft), sources)
            metadata = {"request_id": attempt["request_id"], "response_id": attempt["response_id"],
                        "model": attempt["returned_model"], "service_tier": attempt["service_tier"],
                        "usage": json.loads(attempt["usage_json"] or "{}"), "latency_ms": attempt["latency_ms"],
                        "estimated_cost_usd": attempt["estimated_cost_usd"],
                        "local_repair": {"version": EVIDENCE_VERSION, "repaired_at": now,
                            "original_attempt_id": attempt["attempt_id"], "original_finished_at": attempt["finished_at"],
                            "original_output_sha256": item["output_sha256"], "plan_sha256": plan_hash,
                            "original_error": work["error_message"], "reviewed_corrections": reviewed,
                            "removed_description_languages": removed}}
            document = {"fields": payload, "contract": json.loads(work["contract_json"]), "metadata": metadata,
                        "validation": {"schema": True, "evidence": True, "human_reviewed": False}}
            pending.append((work, document))
            results.append({"work_id": work["work_id"], "job_id": work["job_id"],
                            "status": "repaired" if apply else "ready", "reviewed_corrections": len(reviewed),
                            "removed_description_languages": len(removed)})
        for work, document in pending if apply else []:
            # A repair is a new immutable extraction; its original work fingerprint
            # remains cached so a later bulk click does not generate it again.
            version = work["fingerprint"] + ":repair:" + document["metadata"]["local_repair"]["plan_sha256"]
            extraction_id = conn.execute("""INSERT INTO description_extractions
                (source_id,extractor,extractor_version,schema_version,extracted_at,content_sha256,data_json)
                VALUES (?,'openai_job',?,?,?,?,?)""", (work["source_id"], version,
                document["contract"]["schema_version"], now, digest(document), encode(document))).lastrowid
            conn.execute("""UPDATE ai_work SET status='succeeded',finished_at=?,next_attempt_at=0,
                error_kind=NULL,error_message=NULL,extraction_id=? WHERE work_id=?""", (now, extraction_id, work["work_id"]))
    return {"applied": apply, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = repair_saved_outputs(args.db, json.loads(args.plan.read_text()), apply=args.apply)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(output)
    else:
        print(output, end="")
