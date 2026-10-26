from pathlib import Path
from typing import Dict, List, Optional

from backend.agents.extraction_agent import ExtractionAgent
from backend.schemas.extraction_schema import ExtractionResult
from database import DatabaseHelper


class ExtractionPipelineService:
    """
    End-to-end pipeline: database file records -> Claude (direct Base64) -> merged ExtractionResult.

    Files are sent directly to Claude as Base64 content blocks.
    Claude reads and interprets the raw files with no intermediate parsing step.

    After extraction, module entries are matched against the user_modules table
    and db_module_code / db_module_name fields are populated.
    """

    SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".xlsx"}

    def __init__(self, db: Optional[DatabaseHelper] = None):
        self.db = db or DatabaseHelper()

    # ─────────────────────────────────────────
    # Database → valid file records
    # ─────────────────────────────────────────

    def _get_file_records_for_user(self, user_id: int) -> Dict:
        """
        Fetch uploaded file records from the database and filter to only
        those that exist on disk with a supported extension.
        """
        uploaded_files = self.db.get_uploaded_files_by_user_id(user_id)

        valid_records: List[Dict[str, str]] = []
        skipped_missing: List[str] = []
        skipped_unsupported: List[str] = []

        for item in uploaded_files:
            raw_path = item.get("file_path", "")
            file_role = item.get("file_role", "")

            if not raw_path:
                continue

            file_path = Path(raw_path)
            extension = file_path.suffix.lower()

            if extension not in self.SUPPORTED_EXTENSIONS:
                skipped_unsupported.append(raw_path)
                continue

            if not file_path.exists():
                skipped_missing.append(raw_path)
                continue

            valid_records.append({
                "file_path": str(file_path),
                "file_role": file_role,
            })

        return {
            "uploaded_files_total": len(uploaded_files),
            "valid_records": valid_records,
            "skipped_missing": skipped_missing,
            "skipped_unsupported": skipped_unsupported,
        }

    # ─────────────────────────────────────────
    # Module matching
    # ─────────────────────────────────────────

    def _get_user_module_map(self, user_id: int) -> Dict[str, str]:
        """
        Fetch the user's saved modules from user_modules table.
        Returns a dict: { "IT1123": "Database Design", ... }
        """
        rows = self.db.get_user_modules_by_user_id(user_id)
        return {
            row["module_code"].strip().upper(): row["module_name"].strip()
            for row in rows
            if row.get("module_code")
        }

    def _match_modules_with_db(
        self,
        modules: List[Dict],
        db_module_map: Dict[str, str],
    ) -> List[Dict]:
        """
        For each extracted module, look up its module_code in the user's
        saved modules and fill db_module_code and db_module_name.
        If no match is found, both fields stay as empty string.
        """
        for module in modules:
            extracted_code = (module.get("module_code") or "").strip().upper()
            if extracted_code and extracted_code in db_module_map:
                module["db_module_code"] = extracted_code
                module["db_module_name"] = db_module_map[extracted_code]
            else:
                module["db_module_code"] = ""
                module["db_module_name"] = ""
        return modules

    # ─────────────────────────────────────────
    # Merge helpers
    # ─────────────────────────────────────────

    @staticmethod
    def _empty_merged_result() -> Dict:
        return {
            "modules": [],
            "class_sessions": [],
            "module_schedule": [],
            "assessments": [],
            "special_weeks": [],
            "remarks": [],
        }

    @staticmethod
    def _append_unique_items(target_list: List, new_items: List):
        """Add items from new_items not already in target_list."""
        seen = {repr(item) for item in target_list}
        for item in new_items:
            key = repr(item)
            if key not in seen:
                target_list.append(item)
                seen.add(key)

    # ─────────────────────────────────────────
    # Main pipeline
    # ─────────────────────────────────────────

    def run_for_user(
        self,
        user_id: int,
        prompt_path: str | None = None,
    ) -> Dict:
        """
        Run the full extraction pipeline for a user.

        Flow:
          1. Fetch user's uploaded file records from the database
          2. Fetch user's saved modules from user_modules table
          3. Send each file directly to Claude as a Base64 content block
          4. Claude extracts structured JSON (no pre-parsing step)
          5. Validate JSON with Pydantic (ExtractionResult)
          6. Match extracted modules against user_modules and fill db fields
          7. Merge all per-file results into one combined ExtractionResult
          8. Return a detailed report dict
        """
        db_file_info = self._get_file_records_for_user(user_id)
        file_records = db_file_info["valid_records"]

        if not file_records:
            raise ValueError(
                f"No valid uploaded files found for user_id={user_id}. "
                f"total={db_file_info['uploaded_files_total']} | "
                f"missing={len(db_file_info['skipped_missing'])} | "
                f"unsupported={len(db_file_info['skipped_unsupported'])}"
            )

        db_module_map = self._get_user_module_map(user_id)
        agent = ExtractionAgent(prompt_path=prompt_path)

        merged = self._empty_merged_result()
        per_file_results = []

        for record in file_records:
            file_name = Path(record["file_path"]).name
            file_role = record.get("file_role", "")

            try:
                single_result = agent.extract([record])
                single_dump = single_result.model_dump()

                # Match extracted modules with user's DB modules
                single_dump["modules"] = self._match_modules_with_db(
                    single_dump.get("modules", []),
                    db_module_map,
                )

                for key in merged:
                    self._append_unique_items(merged[key], single_dump.get(key, []))

                per_file_results.append({
                    "file_name": file_name,
                    "file_role": file_role,
                    "status": "success",
                    "result": single_dump,
                })

            except Exception as exc:
                error_text = str(exc)
                if len(error_text) > 3000:
                    error_text = error_text[:3000] + "..."

                merged["remarks"].append(
                    f"Extraction failed for {file_name}: {error_text}"
                )
                per_file_results.append({
                    "file_name": file_name,
                    "file_role": file_role,
                    "status": "error",
                    "error": error_text,
                })

        extraction_result = ExtractionResult(**merged)

        extraction_success_count = sum(1 for r in per_file_results if r["status"] == "success")
        extraction_error_count = sum(1 for r in per_file_results if r["status"] == "error")

        return {
            "user_id": user_id,
            "uploaded_files_total": db_file_info["uploaded_files_total"],
            "files_sent_to_claude": len(file_records),
            "files_extracted_success": extraction_success_count,
            "files_extracted_error": extraction_error_count,
            "skipped_missing": db_file_info["skipped_missing"],
            "skipped_unsupported": db_file_info["skipped_unsupported"],
            "per_file_results": per_file_results,
            "extraction_result": extraction_result.model_dump(),
        }