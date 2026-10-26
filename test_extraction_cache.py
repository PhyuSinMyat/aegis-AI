import json
from pathlib import Path

from database import DatabaseHelper


def _normalize_payload(payload: dict) -> tuple[dict, list[str]]:
    notes = []
    if not isinstance(payload, dict):
        return {"extraction_result": {}}, ["Cached payload is not a JSON object."]

    extraction = payload.get("extraction_result", {})
    if isinstance(extraction, str):
        try:
            maybe_json = json.loads(extraction)
            if isinstance(maybe_json, dict):
                extraction = maybe_json
                notes.append("extraction_result was a JSON string and has been decoded.")
            else:
                extraction = {}
                notes.append("extraction_result was a string, but not a JSON object.")
        except Exception:
            extraction = {}
            notes.append("extraction_result was saved in the old broken format. Re-run Process Timetable to regenerate a proper cache.")

    payload["extraction_result"] = extraction
    for key in ("modules", "class_sessions", "module_schedule", "assessments", "special_weeks", "remarks"):
        payload.setdefault("extraction_result", {}).setdefault(key, [])
    return payload, notes


def main():
    user_id = int(input("Enter user_id: ").strip())
    db = DatabaseHelper()

    cache = db.get_extraction_cache(user_id)
    if not cache:
        print(f"No extraction cache found for user_id={user_id}")
        return

    try:
        payload = json.loads(cache["result_json"])
    except Exception as exc:
        print(f"Failed to parse cached JSON: {exc}")
        return

    payload, notes = _normalize_payload(payload)

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"extraction_cache_user_{user_id}.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    extraction = payload.get("extraction_result", {})
    print("Saved:", output_path)
    print("cached_at:", cache.get("cached_at"))
    for note in notes:
        print("note:", note)
    print("modules:", len(extraction.get("modules", [])))
    print("class_sessions:", len(extraction.get("class_sessions", [])))
    print("module_schedule:", len(extraction.get("module_schedule", [])))
    print("assessments:", len(extraction.get("assessments", [])))
    print("special_weeks:", len(extraction.get("special_weeks", [])))
    print("remarks:", len(extraction.get("remarks", [])))


if __name__ == "__main__":
    main()
