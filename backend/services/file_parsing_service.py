import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
import pdfplumber


class FileParsingService:
    """
    Parse uploaded timetable files into normalized tagged text blocks.

    Class timetable:
    - keeps cell-based extraction

    Module timetable:
    - uses pdfplumber table extraction first
    - handles continuation rows across pages
    - outputs clean structured blocks for extraction agent
    - no hardcoded module names/content
    """

    SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".xlsx"}
    DAY_HEADERS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}

    # -----------------------------
    # SHARED HELPERS
    # -----------------------------
    @staticmethod
    def _stringify_cell(value) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _safe_upper(text: str) -> str:
        return (text or "").strip().upper()

    @staticmethod
    def _safe_lower(text: str) -> str:
        return (text or "").strip().lower()

    @classmethod
    def _normalize_table_row(cls, row) -> List[str]:
        return [cls._stringify_cell(cell) for cell in (row or [])]

    @classmethod
    def _clean_text(cls, text: str) -> str:
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
        text = text.replace("–", "-").replace("—", "-")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def _clean_multiline_cell(cls, text: str) -> str:
        text = cls._clean_text(text)
        if not text:
            return ""
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
        lines = [line for line in lines if line]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_first_week_number(text: str) -> Optional[str]:
        """
        Accept:
        1
        Week 1
        Wk 1
        1 something...
        """
        text = (text or "").strip()

        if not text:
            return None

        match = re.search(r"(?i)\b(?:week|wk)?\s*(\d{1,2})\b", text)
        if match:
            return match.group(1)

        return None

    @classmethod
    def _normalize_mode(cls, text: str) -> str:
        text = cls._clean_multiline_cell(text).replace("\n", " ").strip()
        text = re.sub(r"\s*-\s*", "-", text)
        text = re.sub(r"\s+", " ", text)

        low = text.lower()

        mapping = {
            "async-online": "Async-Online",
            "sync-physical": "Sync-Physical",
            "sync-online": "Sync-Online",
            "async-physical": "Async-Physical",
            "async physical": "Async-Physical",
            "sync physical": "Sync-Physical",
            "sync online": "Sync-Online",
            "async online": "Async-Online",
            "online": "Online",
            "physical": "Physical",
            "async": "Async",
            "sync": "Sync",
        }
        return mapping.get(low, text)

    @staticmethod
    def _is_placeholder_text(text: str) -> bool:
        return (text or "").strip() in {"", "-", "—", "NIL", "Nil", "nil", "None"}

    @classmethod
    def _looks_like_module_header_row(cls, row: List[str]) -> bool:
        """
        Detect:
        Week | Sub-Tasks / Activities | Hours | Mode | Assessment

        Also tolerate partial/continued header variants.
        """
        if not row:
            return False

        joined = " | ".join(cls._safe_lower(cell) for cell in row)
        return (
            "week" in joined
            and "hours" in joined
            and "mode" in joined
            and ("activities" in joined or "sub-tasks" in joined or "sub tasks" in joined)
        )

    @classmethod
    def _looks_like_lesson_plan_header_row(cls, row: List[str]) -> bool:
        """
        Detect:
        Week | Lecture | Lab | Remarks
        """
        if not row:
            return False

        joined = " | ".join(cls._safe_lower(cell) for cell in row)
        return (
            "week" in joined
            and ("lecture" in joined or "lab" in joined)
            and ("remarks" in joined or "date" in joined or "date starting" in joined)
        )

    # -----------------------------
    # PDF TYPE DETECTION
    # -----------------------------
    @classmethod
    def _detect_pdf_type(cls, first_page_text: str, file_role: Optional[str] = None) -> str:
        role = (file_role or "").strip().lower()
        text_upper = cls._safe_upper(first_page_text)

        if role in {"class_timetable", "module_timetable"}:
            return role

        if "TIMETABLE" in text_upper:
            return "class_timetable"

        if "TIME" in text_upper and "MON" in text_upper and "TUE" in text_upper and "FRI" in text_upper:
            return "class_timetable"

        return "module_timetable"

    # -----------------------------
    # CLASS TIMETABLE PARSING
    # -----------------------------
    @classmethod
    def _looks_like_day_header(cls, row: List[str]) -> bool:
        if not row or len(row) < 3:
            return False

        normalized = [cls._safe_upper(cell) for cell in row]
        first_cell = normalized[0] if normalized else ""
        day_count = sum(1 for cell in normalized if cell in cls.DAY_HEADERS)

        first_is_time_like = first_cell in {"TIME", "DAY/TIME", "TIMING", "HOUR"} or "TIME" in first_cell
        return first_is_time_like and day_count >= 2

    @classmethod
    def _format_class_timetable_table(cls, page_number: int, table: List[List[str]]) -> str:
        if not table:
            return ""

        header = table[0]
        day_labels = []

        for index, cell in enumerate(header):
            upper_cell = cls._safe_upper(cell)
            if index == 0:
                day_labels.append("TIME")
            elif upper_cell in cls.DAY_HEADERS:
                day_labels.append(upper_cell)
            else:
                day_labels.append(cell.strip() or f"COLUMN_{index}")

        blocks = []

        for row in table[1:]:
            if not row:
                continue

            time_value = cls._clean_multiline_cell(row[0]) if len(row) > 0 else ""
            if not time_value:
                continue

            for col_index in range(1, min(len(row), len(day_labels))):
                day_value = day_labels[col_index].strip()
                cell_value = cls._clean_multiline_cell(row[col_index])

                if not cell_value:
                    continue

                block = [
                    "[CLASS TIMETABLE CELL]",
                    f"Page: {page_number}",
                    f"Day: {day_value}",
                    f"Time: {time_value}",
                    "Content:",
                    cell_value,
                ]
                blocks.append("\n".join(block))

        return "\n\n".join(blocks).strip()

    @classmethod
    def _extract_class_timetable_pdf(cls, file_path: Path) -> str:
        parts = []

        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []

                for raw_table in tables:
                    normalized_table = [cls._normalize_table_row(row) for row in raw_table if row]
                    normalized_table = [row for row in normalized_table if any(cell.strip() for cell in row)]

                    if not normalized_table:
                        continue

                    if cls._looks_like_day_header(normalized_table[0]):
                        structured = cls._format_class_timetable_table(page_number, normalized_table)
                        if structured:
                            parts.append(structured)

        return cls._clean_text("\n\n".join(parts))

    # -----------------------------
    # MODULE TIMETABLE PARSING
    # -----------------------------
    @classmethod
    def _extract_tables_from_page(cls, page) -> List[List[List[str]]]:
        """
        Extract all tables from page and normalize every cell to string.
        """
        raw_tables = page.extract_tables() or []
        tables = []

        for raw_table in raw_tables:
            normalized_table = [cls._normalize_table_row(row) for row in raw_table if row]
            normalized_table = [row for row in normalized_table if any(cell.strip() for cell in row)]
            if normalized_table:
                tables.append(normalized_table)

        return tables

    @classmethod
    def _looks_like_module_continuation_table(cls, table: List[List[str]]) -> bool:
        """
        A continuation page may not repeat the header.
        We treat it as a module table if:
        - rows have around 4-5 columns
        - there are hours/mode patterns
        """
        if not table:
            return False

        sample_rows = table[:5]
        for row in sample_rows:
            joined = " | ".join(cls._safe_lower(cell) for cell in row)
            if "async" in joined or "sync" in joined or "online" in joined or "physical" in joined:
                return True

        return False

    @classmethod
    def _table_is_module_learning_plan(cls, table: List[List[str]]) -> bool:
        if not table:
            return False

        first_row = table[0]
        if cls._looks_like_module_header_row(first_row):
            return True

        return cls._looks_like_module_continuation_table(table)

    @classmethod
    def _table_is_module_lesson_plan(cls, table: List[List[str]]) -> bool:
        if not table:
            return False

        first_row = table[0]
        return cls._looks_like_lesson_plan_header_row(first_row)

    @classmethod
    def _normalize_learning_plan_row(
        cls,
        row: List[str],
        current_week: Optional[str],
    ) -> Optional[Dict]:
        """
        Normalize one row from a module learning plan table:
        Week | Sub-Tasks / Activities | Hours | Mode | Assessment

        Handles continuation rows where week is blank.
        """
        if not row:
            return None

        padded = row[:] + [""] * max(0, 5 - len(row))
        padded = padded[:5]

        raw_week = cls._clean_multiline_cell(padded[0])
        raw_activity = cls._clean_multiline_cell(padded[1])
        raw_hours = cls._clean_multiline_cell(padded[2])
        raw_mode = cls._clean_multiline_cell(padded[3])
        raw_assessment = cls._clean_multiline_cell(padded[4])

        # skip accidental header row
        joined = " | ".join([raw_week, raw_activity, raw_hours, raw_mode, raw_assessment]).lower()
        if (
            "week" in joined
            and "hours" in joined
            and "mode" in joined
            and ("activities" in joined or "sub-tasks" in joined or "sub tasks" in joined)
        ):
            return None

        week = cls._extract_first_week_number(raw_week) or current_week

        # hours cleanup
        hours = ""
        if raw_hours:
            number_match = re.search(r"\d+(?:\.\d+)?", raw_hours)
            if number_match:
                hours = number_match.group(0)

        mode = cls._normalize_mode(raw_mode)

        activity = raw_activity
        assessment = raw_assessment

        if cls._is_placeholder_text(activity):
            activity = ""

        if cls._is_placeholder_text(assessment):
            assessment = ""

        if cls._is_placeholder_text(mode):
            mode = ""

        # if row is effectively empty, skip it
        if not any([week, activity, hours, mode, assessment]):
            return None

        return {
            "week": week or "",
            "activities": activity,
            "hours": hours,
            "mode": mode,
            "assessment": assessment,
        }

    @classmethod
    def _normalize_lesson_plan_row(cls, row: List[str], current_week: Optional[str]) -> Optional[Dict]:
        """
        Normalize:
        Week | Lecture | Lab | Remarks
        into the same output structure expected by extraction agent.
        """
        if not row:
            return None

        padded = row[:] + [""] * max(0, 4 - len(row))
        padded = padded[:4]

        raw_week = cls._clean_multiline_cell(padded[0])
        lecture = cls._clean_multiline_cell(padded[1])
        lab = cls._clean_multiline_cell(padded[2])
        remarks = cls._clean_multiline_cell(padded[3])

        joined = " | ".join([raw_week, lecture, lab, remarks]).lower()
        if "week" in joined and ("lecture" in joined or "lab" in joined):
            return None

        week = cls._extract_first_week_number(raw_week) or current_week

        parts = []
        if lecture:
            parts.append(f"Lecture: {lecture}")
        if lab:
            parts.append(f"Lab: {lab}")

        activities = "\n".join(parts).strip()

        if cls._is_placeholder_text(activities) and cls._is_placeholder_text(remarks):
            return None

        return {
            "week": week or "",
            "activities": activities,
            "hours": "",
            "mode": "",
            "assessment": remarks if not cls._is_placeholder_text(remarks) else "",
        }

    @classmethod
    def _merge_or_append_module_row(cls, rows: List[Dict], row: Dict) -> None:
        """
        Merge assessment-only / continuation-only lines into previous row if suitable.
        """
        if not row:
            return

        if not rows:
            rows.append(row)
            return

        prev = rows[-1]

        same_week = (prev.get("week") or "") == (row.get("week") or "")

        # assessment-only continuation
        if same_week and not row.get("activities") and not row.get("hours") and not row.get("mode") and row.get("assessment"):
            prev["assessment"] = "\n".join(filter(None, [prev.get("assessment", "").strip(), row["assessment"].strip()])).strip()
            return

        # activity-only continuation
        if same_week and row.get("activities") and not row.get("hours") and not row.get("mode") and not row.get("assessment"):
            prev["activities"] = "\n".join(filter(None, [prev.get("activities", "").strip(), row["activities"].strip()])).strip()
            return

        # if week missing because of page continuation, attach to previous if row looks like second half
        if same_week and row.get("activities", "").startswith("Activity:") and prev.get("activities") and not prev.get("activities", "").startswith("Activity:"):
            rows.append(row)
            return

        rows.append(row)

    @classmethod
    def _format_module_rows(cls, rows: List[Dict]) -> str:
        """
        Output format designed for extraction agent.
        """
        blocks = []

        for row in rows:
            week = (row.get("week") or "").strip()
            activities = (row.get("activities") or "").strip()
            hours = (row.get("hours") or "").strip()
            mode = (row.get("mode") or "").strip()
            assessment = (row.get("assessment") or "").strip()
            page = row.get("page")

            if not any([week, activities, hours, mode, assessment]):
                continue

            block = [
                "[MODULE TIMETABLE ROW]",
                f"Page: {page}",
                f"Week: {week}",
                "Activities:",
                activities if activities else "-",
                f"Hours: {hours if hours else '-'}",
                f"Mode: {mode if mode else '-'}",
                f"Assessment: {assessment if assessment else '-'}",
            ]
            blocks.append("\n".join(block))

        return "\n\n".join(blocks).strip()

    @classmethod
    def _parse_module_learning_plan_pdf(cls, file_path: Path) -> List[Dict]:
        rows: List[Dict] = []
        current_week: Optional[str] = None

        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_tables = cls._extract_tables_from_page(page)
                if not page_tables:
                    continue

                for table in page_tables:
                    if not cls._table_is_module_learning_plan(table):
                        continue

                    start_index = 1 if cls._looks_like_module_header_row(table[0]) else 0

                    for row in table[start_index:]:
                        item = cls._normalize_learning_plan_row(row, current_week=current_week)
                        if not item:
                            continue

                        if item.get("week"):
                            current_week = item["week"]

                        item["page"] = page_number
                        cls._merge_or_append_module_row(rows, item)

        return rows

    @classmethod
    def _parse_module_lesson_plan_pdf(cls, file_path: Path) -> List[Dict]:
        rows: List[Dict] = []
        current_week: Optional[str] = None

        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_tables = cls._extract_tables_from_page(page)
                if not page_tables:
                    continue

                for table in page_tables:
                    if not cls._table_is_module_lesson_plan(table):
                        continue

                    for row in table[1:]:
                        item = cls._normalize_lesson_plan_row(row, current_week=current_week)
                        if not item:
                            continue

                        if item.get("week"):
                            current_week = item["week"]

                        item["page"] = page_number
                        cls._merge_or_append_module_row(rows, item)

        return rows

    @classmethod
    def _extract_module_timetable_pdf(cls, file_path: Path) -> str:
        learning_plan_rows = cls._parse_module_learning_plan_pdf(file_path)
        if learning_plan_rows:
            return cls._format_module_rows(learning_plan_rows)

        lesson_plan_rows = cls._parse_module_lesson_plan_pdf(file_path)
        if lesson_plan_rows:
            return cls._format_module_rows(lesson_plan_rows)

        return ""

    # -----------------------------
    # CSV / XLSX
    # -----------------------------
    @classmethod
    def parse_csv(cls, file_path: Path) -> str:
        rows = []
        with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for row_index, row in enumerate(reader, start=1):
                cells = [cls._stringify_cell(cell) for cell in row]
                if any(cells):
                    row_text = " | ".join(cell for cell in cells if cell)
                    rows.append(f"Row {row_index}: {row_text}")
        return cls._clean_text("\n".join(rows))

    @classmethod
    def parse_excel(cls, file_path: Path) -> str:
        workbook = openpyxl.load_workbook(file_path, data_only=True)
        parts = []

        for sheet in workbook.worksheets:
            sheet_rows = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = [cls._stringify_cell(cell) for cell in row]
                if any(cells):
                    row_text = " | ".join(cell for cell in cells if cell)
                    sheet_rows.append(f"Row {row_index}: {row_text}")

            if sheet_rows:
                parts.append(f"[SHEET: {sheet.title}]\n" + "\n".join(sheet_rows))

        return cls._clean_text("\n\n".join(parts))

    # -----------------------------
    # MAIN
    # -----------------------------
    @classmethod
    def parse_pdf(cls, file_path: Path, file_role: Optional[str] = None) -> Tuple[str, str]:
        with pdfplumber.open(file_path) as pdf:
            first_page_text = ""
            if pdf.pages:
                first_page_text = cls._clean_text(pdf.pages[0].extract_text() or "")

        pdf_type = cls._detect_pdf_type(first_page_text, file_role=file_role)

        if pdf_type == "class_timetable":
            return cls._extract_class_timetable_pdf(file_path), pdf_type

        return cls._extract_module_timetable_pdf(file_path), "module_timetable"

    @classmethod
    def parse_one_file(cls, file_record: Dict) -> Dict:
        file_path = Path(file_record.get("file_path", ""))
        file_role = file_record.get("file_role", "")
        extension = file_path.suffix.lower()

        if extension not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {extension}")

        detected_role = file_role or ""

        if extension == ".pdf":
            text, detected_role = cls.parse_pdf(file_path, file_role=file_role)
        elif extension == ".csv":
            text = cls.parse_csv(file_path)
        else:
            text = cls.parse_excel(file_path)

        return {
            "file_name": file_path.name,
            "file_path": str(file_path),
            "file_role": detected_role,
            "text": text,
        }

    @classmethod
    def parse_files(cls, file_records: List[Dict]) -> Dict:
        parsed_files = []
        parse_errors = []

        for item in file_records:
            file_path = Path(item.get("file_path", ""))

            try:
                parsed_files.append(cls.parse_one_file(item))
            except Exception as exc:
                parsed_files.append(
                    {
                        "file_name": file_path.name,
                        "file_path": str(file_path),
                        "file_role": item.get("file_role", ""),
                        "text": "",
                        "parse_error": str(exc),
                    }
                )
                parse_errors.append(
                    {
                        "file_name": file_path.name,
                        "error": str(exc),
                    }
                )

        combined_text = "\n\n".join(
            (item.get("text") or "").strip()
            for item in parsed_files
            if (item.get("text") or "").strip()
        )

        return {
            "combined_text": cls._clean_text(combined_text),
            "files": parsed_files,
            "parse_errors": parse_errors,
        }
