import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backend.schemas.extraction_schema import ExtractionResult
from llm_service import file_to_base64_content_block, generate_response, load_extraction_prompt


class ExtractionAgent:
    """
    Reads files directly from disk as Base64 content blocks,
    sends them to Claude with the extraction prompt,
    and validates the JSON response against ExtractionResult.

    No file parsing step — Claude reads the raw files itself.
    """

    def __init__(
        self,
        prompt_path: str | None = None,
        llm_callable: Callable[..., str] | None = None,
    ):
        self.system_prompt = load_extraction_prompt(prompt_path)
        # Allow injecting a custom LLM callable (useful for testing)
        self._llm_callable = llm_callable or generate_response

    # ─────────────────────────────────────────
    # Content block builder
    # ─────────────────────────────────────────

    @staticmethod
    def _build_content_blocks(file_records: List[Dict]) -> List[dict]:
        """
        Convert a list of file records into Bedrock content blocks.

        Each file record must have:
          - file_path: str  (absolute or relative path on disk)
          - file_role: str  ("class_timetable" or "module_timetable")

        Returns a flat list of document/text blocks ready for the Bedrock API.
        """
        blocks = []

        for record in file_records:
            file_path = Path(record["file_path"])
            file_role = record.get("file_role", "")

            if not file_path.exists():
                raise FileNotFoundError(f"File not found on disk: {file_path}")

            blocks.extend(file_to_base64_content_block(file_path, file_role))

        return blocks

    # ─────────────────────────────────────────
    # JSON cleanup
    # ─────────────────────────────────────────

    @staticmethod
    def _clean_json_response(raw: str) -> str:
        """
        Strip any markdown code fences the LLM may have added
        despite being told not to, then return clean JSON.
        """
        # Remove ```json ... ``` or ``` ... ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        return cleaned.strip()

    # ─────────────────────────────────────────
    # Main extract method
    # ─────────────────────────────────────────

    def extract(self, file_records: List[Dict]) -> ExtractionResult:
        """
        Full extraction for a list of file records.

        Steps:
          1. Build content blocks from files on disk (Base64 PDF / plain text CSV/XLSX)
          2. Append the extraction prompt as the final text block
          3. Send everything to Claude via llm_service
          4. Parse and validate the JSON response with Pydantic

        Returns an ExtractionResult instance.
        """
        content_blocks = self._build_content_blocks(file_records)

        # Append the instruction prompt as the last content block
        content_blocks.append({
            "text": (
                "Now extract all academic information from the documents above "
                "and return STRICT VALID JSON matching the schema exactly.\n\n"
                + self.system_prompt
            ),
        })

        raw_response = self._llm_callable(
            content_blocks=content_blocks,
            system_prompt=None,  # Instructions are in the user turn above
        )

        cleaned = self._clean_json_response(raw_response)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM returned invalid JSON: {exc}\n"
                f"Raw response (first 500 chars): {raw_response[:500]}"
            )

        return ExtractionResult(**data)