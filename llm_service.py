import json
import time
from pathlib import Path
from typing import List, Optional

from config import Config


# ─────────────────────────────────────────
# Prompt loader
# ─────────────────────────────────────────

def load_extraction_prompt(prompt_path: str | None = None) -> str:
    prompt_file = Path(prompt_path) if prompt_path else Config.EXTRACTION_PROMPT_PATH
    if not prompt_file.exists():
        raise FileNotFoundError(f"Extraction prompt file not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8").strip()


# ─────────────────────────────────────────
# Mock response (for local testing without AWS)
# ─────────────────────────────────────────

def build_empty_extraction_json() -> str:
    return json.dumps(
        {
            "modules": [],
            "class_sessions": [],
            "module_schedule": [],
            "weekly_topics": [],
            "assessments": [],
            "milestones": [],
            "special_weeks": [],
            "assumptions": ["Mock LLM response was used."],
        },
        ensure_ascii=False,
    )


# ─────────────────────────────────────────
# File → Base64 content block
# ─────────────────────────────────────────

def file_to_base64_content_block(file_path: Path, file_role: str) -> List[dict]:
    """
    Read a file from disk and return Bedrock Converse API content blocks.

    The Converse API uses a different structure from invoke_model:
      PDF  -> {"document": {"name": ..., "format": "pdf", "source": {"bytes": <bytes>}}}
      text -> {"text": "..."}

    No Base64 encoding needed — Converse API accepts raw bytes directly.
    CSV and XLSX are converted to plain text and sent as text blocks.
    """
    extension = file_path.suffix.lower()
    raw_bytes = file_path.read_bytes()

    role_label = "class timetable" if file_role == "class_timetable" else "module timetable"

    if extension == ".pdf":
        return [
            {
                "document": {
                    "name": file_path.stem[:100],
                    "format": "pdf",
                    "source": {
                        "bytes": raw_bytes,
                    },
                },
            },
            {
                "text": f'The document above is the {role_label}: "{file_path.name}".',
            },
        ]

    # CSV / XLSX — read as plain text and send as a text block
    if extension == ".csv":
        try:
            text_content = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("latin-1")
        return [
            {
                "text": (
                    f'The following is the {role_label}: "{file_path.name}" '
                    f"(CSV format):\n\n{text_content}"
                ),
            }
        ]

    if extension == ".xlsx":
        import openpyxl
        import io
        workbook = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        parts = []
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append(f"[Sheet: {sheet.title}]\n" + "\n".join(rows))
        text_content = "\n\n".join(parts)
        return [
            {
                "text": (
                    f'The following is the {role_label}: "{file_path.name}" '
                    f"(Excel format):\n\n{text_content}"
                ),
            }
        ]

    raise ValueError(f"Unsupported file extension: {extension}")


# ─────────────────────────────────────────
# Bedrock client
# ─────────────────────────────────────────

def _get_bedrock_client():
    import boto3
    from botocore.config import Config as BotoConfig

    boto_config = BotoConfig(
        region_name=Config.AWS_REGION,
        connect_timeout=Config.BEDROCK_CONNECT_TIMEOUT,
        read_timeout=Config.BEDROCK_READ_TIMEOUT,
        retries={"max_attempts": Config.BEDROCK_MAX_ATTEMPTS, "mode": "standard"},
    )
    session = boto3.Session(**Config.bedrock_session_kwargs())
    return session.client("bedrock-runtime", config=boto_config)


def _extract_text_response(response: dict) -> str:
    output = response.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])

    text_parts = [block["text"] for block in content if block.get("text")]
    result = "\n".join(text_parts).strip()

    stop_reason = response.get("stopReason") or output.get("stopReason")
    if Config.DEBUG_LLM and stop_reason:
        print(f"[LLM stop_reason] {stop_reason}")

    usage = response.get("usage", {})
    if Config.DEBUG_LLM and usage:
        print(f"[LLM usage] {usage}")

    if not result:
        raise RuntimeError(f"Bedrock returned no text content. Response: {response}")

    return result


# ─────────────────────────────────────────
# Main callable — used by ExtractionAgent
# ─────────────────────────────────────────

def generate_response(
    content_blocks: List[dict],
    system_prompt: Optional[str] = None,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Send a list of Bedrock content blocks (documents + text) to Claude
    and return the raw text response.

    content_blocks is a list built by the agent — it contains the file
    document blocks followed by the instruction prompt as a text block.
    """
    if Config.USE_MOCK_LLM:
        return build_empty_extraction_json()

    bedrock = _get_bedrock_client()
    resolved_model_id = Config.resolve_bedrock_model_id(model_id)

    params = {
        "modelId": resolved_model_id,
        "messages": [
            {
                "role": "user",
                "content": content_blocks,
            }
        ],
        "inferenceConfig": {
            "maxTokens": max_tokens if max_tokens is not None else Config.BEDROCK_MAX_TOKENS,
            "temperature": Config.BEDROCK_TEXT_TEMPERATURE,
        },
    }

    if system_prompt:
        params["system"] = [{"text": system_prompt}]

    last_error = None

    for attempt in range(1, Config.BEDROCK_API_RETRIES + 1):
        try:
            response = bedrock.converse(**params)
            return _extract_text_response(response)
        except Exception as exc:
            last_error = exc
            if attempt == Config.BEDROCK_API_RETRIES:
                break
            wait = min(2 ** (attempt - 1), 4)
            if Config.DEBUG_LLM:
                print(f"[LLM] Attempt {attempt} failed: {exc}. Retrying in {wait}s...")
            time.sleep(wait)

    raise RuntimeError(f"Bedrock request failed after {Config.BEDROCK_API_RETRIES} attempts: {last_error}")