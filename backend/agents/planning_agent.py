"""
planning_agent.py
─────────────────
Builds the study-plan prompt from a user's full context, streams the
response from Claude via Bedrock, validates the JSON sessions output,
and HARD-ENFORCES blocked time constraints after generation.

What this version fixes:
- all occupied_times rows are enforced, not just one
- class sessions are also enforced as blocked windows
- overlapping study sessions are shifted to valid free slots on the same day
- if a day has no valid slot left, the session is dropped instead of violating constraints
- allowed study days and preferred study time are respected during repair
"""

from __future__ import annotations

import copy
import json as _json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.schemas.planning_schema import StudyPlanResult

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "planning_prompt.txt"

PLANNING_SYSTEM_PROMPT = """
You are a study-planning formatter.
Return final answer only.
Do NOT show reasoning, working, analysis, pre-planning notes, or chain-of-thought.
Do NOT explain your steps.
Follow the user's required output format exactly.
If you cannot fully satisfy a preference, still return the best valid final answer in the required format.
""".strip()

REPAIR_SYSTEM_PROMPT = """
You repair study-plan outputs into strict machine-readable JSON.
Return JSON only. No markdown. No code fences. No commentary.
Schema:
{
  "sessions": [
    {
      "day": "Monday",
      "start": "19:00",
      "end": "20:00",
      "subject": "Database Design",
      "topic": "SQL joins for Tuesday lesson",
      "type": "study"
    }
  ],
  "summary": "...",
  "tips": "...",
  "alerts": "..."
}
Rules:
- sessions must contain STUDY sessions only
- type must be "study" unless the input clearly contains another valid study type
- missing fields become empty strings
- preserve meaning; do not invent classes or deadlines unless strongly supported by the input text
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt loader
# ─────────────────────────────────────────────────────────────────────────────

def load_planning_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Preference label maps
# ─────────────────────────────────────────────────────────────────────────────

SESSION_LENGTH_LABELS: Dict[str, str] = {
    "25": "25 minutes (Pomodoro style)",
    "45": "45 minutes (focused sprint)",
    "60": "60 minutes (one solid hour)",
    "90": "90 minutes (deep work block)",
}

BREAK_LENGTH_LABELS: Dict[str, str] = {
    "short": "5 minutes",
    "medium": "15 minutes",
    "long": "30 minutes",
}

# Numeric break durations used by the constraint enforcer and prompt.
# These are the exact break durations selected by the student.
BREAK_LENGTH_MINS: Dict[str, int] = {
    "short": 5,
    "medium": 15,
    "long": 30,
}

INTENSITY_HOURS: Dict[str, tuple] = {
    "relaxed": ("Just Keeping Up", 10, 15),
    "just keeping up": ("Just Keeping Up", 10, 15),
    "balanced": ("Steady Progress", 16, 25),
    "steady progress": ("Steady Progress", 16, 25),
    "focused": ("High Effort", 26, 35),
    "high effort": ("High Effort", 26, 35),
    "intensive": ("Full Grind", 36, 45),
    "intense": ("Full Grind", 36, 45),
    "full grind": ("Full Grind", 36, 45),
}


def build_study_time_window_rule(study_time_pref: str) -> str:
    pref = (study_time_pref or "").strip()

    if pref == "Morning":
        return (
            "MORNING PREFERENCE — prefer the earliest realistic free study slot on each allowed day. "
            "Treat this as a priority, not a rigid clock cutoff. "
            "Never overlap with class sessions or recurring commitments. "
            "Do NOT use short lunch breaks or small gaps between classes as study sessions if the student has class blocks before and after. "
            "If the student has an early class-heavy day, and the only remaining morning gap is just a lunch break or a short between-class gap, "
            "move the study session to a more realistic later free slot that day, including evening if needed."
        )

    if pref == "Afternoon":
        return (
            "AFTERNOON PREFERENCE — prefer a later daytime free slot on each allowed day, usually after earlier obligations have finished. "
            "Treat this as a priority, not a rigid fixed-hour rule. "
            "Never overlap with class sessions or recurring commitments. "
            "Do NOT use short lunch breaks or small gaps between classes as study sessions if the student has class blocks before and after. "
            "If the only afternoon gap is an unrealistic lunch break or a short between-class gap, use a more realistic later free slot that day instead."
        )

    if pref == "Night":
        return (
            "NIGHT PREFERENCE — prefer the latest realistic free slot on each allowed day. "
            "Treat this as a priority, not a rigid fixed-hour rule. "
            "Never overlap with class sessions or recurring commitments. "
            "If the student has evening classes, work, or other commitments, use the next best realistic free slot earlier that day instead."
        )

    return (
        "Any realistic free slot is acceptable, as long as it does not overlap with class sessions or recurring commitments. "
        "Do NOT use short lunch breaks or small gaps between classes as study sessions if the student has class blocks before and after."
    )


DAY_ABBREV_MAP: Dict[str, str] = {
    "Mon": "Monday",
    "Tue": "Tuesday",
    "Wed": "Wednesday",
    "Thu": "Thursday",
    "Fri": "Friday",
    "Sat": "Saturday",
    "Sun": "Sunday",
}

DAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]
DAY_INDEX = {day: idx for idx, day in enumerate(DAY_ORDER)}
# Do NOT place study sessions into tiny between-class gaps.
# Example: class 10:00-11:00 and class 12:00-13:00 => the 11:00-12:00 gap is blocked.
MIN_MIDDLE_GAP_FOR_STUDY_MINS = 120   # 2 hours minimum for a middle-of-day gap
DAY_START_MINS = 6 * 60
DAY_END_MINS = 23 * 60
LUNCH_START_MINS = 12 * 60        # 12:00
LUNCH_END_MINS = 13 * 60 + 30     # 13:30
EVENING_START_MINS = 17 * 60      # 17:00
EVENING_CORE_START_MINS = 18 * 60 # 18:00

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_day_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = raw[:3].title() if len(raw) <= 3 else raw.capitalize()
    return DAY_ABBREV_MAP.get(raw, raw)


def _parse_allowed_study_days(study_days_raw: str) -> List[str]:
    days = []
    for part in (study_days_raw or "").split(","):
        day = _normalize_day_name(part)
        if day in DAY_ORDER and day not in days:
            days.append(day)
    return days or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _candidate_prep_days_for_class_day(class_day: str) -> List[str]:
    if class_day not in DAY_INDEX:
        return []
    if class_day == "Monday":
        return ["Sunday", "Saturday", "Friday", "Thursday", "Wednesday", "Tuesday", "Monday"]

    idx = DAY_INDEX[class_day]
    return [DAY_ORDER[(idx - offset) % 7] for offset in range(1, 8)]


def _pick_prep_day(class_day: str, allowed_study_days: List[str]) -> Optional[str]:
    for candidate in _candidate_prep_days_for_class_day(class_day):
        if candidate in allowed_study_days:
            return candidate
    return None


def _parse_iso_date(value: str) -> Optional[date]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_academic_week(semester_start_date: str, today_date: Optional[str] = None) -> Optional[int]:
    sem_start = _parse_iso_date(semester_start_date)
    if sem_start is None:
        return None

    if today_date:
        raw_today = today_date.split(" ")[0].strip()
        today_obj = _parse_iso_date(raw_today)
    else:
        today_obj = date.today()

    if today_obj is None:
        return None

    if today_obj < sem_start:
        return 1

    return ((today_obj - sem_start).days // 7) + 1


def _safe_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Term-break detection helpers
# ─────────────────────────────────────────────────────────────────────────────

_TERM_BREAK_KEYWORDS = {
    "term break", "semester break", "mid-semester break", "mid semester break",
    "recess week", "reading week", "study break", "holiday week", "break week",
}


def get_term_break_weeks(extraction_result) -> List[int]:
    """
    Scan the special_weeks list from an ExtractionResult and return all
    week numbers that are labelled as a term/semester/recess break.

    Works with both a Pydantic ExtractionResult object and a plain dict.
    Returns a sorted list of int week numbers (may be empty).
    """
    if extraction_result is None:
        return []

    data = extraction_result.model_dump() if hasattr(extraction_result, "model_dump") else {}
    special_weeks = data.get("special_weeks", []) or []

    break_weeks: List[int] = []
    for entry in special_weeks:
        label = (entry.get("label") or "").strip().lower()
        week_num = _safe_int(entry.get("week_number", ""))
        if week_num is None:
            continue
        if any(kw in label for kw in _TERM_BREAK_KEYWORDS):
            if week_num not in break_weeks:
                break_weeks.append(week_num)

    return sorted(break_weeks)


def is_term_break_week(week_number: Optional[int], extraction_result) -> bool:
    """Return True if the given week_number is a term-break week."""
    if week_number is None:
        return False
    return week_number in get_term_break_weeks(extraction_result)


def _time_to_minutes(value: str) -> Optional[int]:
    raw = (value or "").strip()
    if not raw:
        return None

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.hour * 60 + dt.minute
        except ValueError:
            continue
    return None


def _minutes_to_hhmm(value: int) -> str:
    h = max(0, min(23, value // 60))
    m = max(0, min(59, value % 60))
    return f"{h:02d}:{m:02d}"


def _merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    cleaned = [(s, e) for s, e in intervals if s is not None and e is not None and s < e]
    if not cleaned:
        return []

    cleaned.sort(key=lambda x: x[0])
    merged = [cleaned[0]]

    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


def _round_up_minutes(value: int, step: int = 5) -> int:
    if step <= 1:
        return value
    remainder = value % step
    return value if remainder == 0 else value + (step - remainder)


def _round_down_minutes(value: int, step: int = 5) -> int:
    if step <= 1:
        return value
    return value - (value % step)


def _pick_closest_slot_to_target(
    free_windows: List[Tuple[int, int]],
    duration_mins: int,
    target_start_mins: int,
) -> Optional[Tuple[int, int]]:
    candidates: List[Tuple[int, int]] = []

    for free_start, free_end in free_windows:
        if free_end - free_start < duration_mins:
            continue

        candidate_start = max(free_start, target_start_mins)
        candidate_start = _round_up_minutes(candidate_start, 5)
        if candidate_start + duration_mins <= free_end:
            candidates.append((candidate_start, candidate_start + duration_mins))
            continue

        fallback_start = _round_down_minutes(free_end - duration_mins, 5)
        if fallback_start >= free_start:
            candidates.append((fallback_start, fallback_start + duration_mins))

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda slot: (
            abs(slot[0] - target_start_mins),
            slot[0],
        ),
    )


def _infer_evening_target_start(
    free_windows: List[Tuple[int, int]],
    blocked_windows: Optional[List[Tuple[int, int]]],
    duration_mins: int,
    total_day_study_mins: Optional[int] = None,
    obligation_blocked_windows: Optional[List[Tuple[int, int]]] = None,
) -> Optional[int]:
    evening_windows = [
        (max(start, EVENING_START_MINS), end)
        for start, end in free_windows
        if end - max(start, EVENING_START_MINS) >= duration_mins
    ]
    evening_windows = [(start, end) for start, end in evening_windows if end > start]
    if not evening_windows:
        return None

    # Use obligation-only windows (class/commitment blocks) for the recovery
    # buffer calculation so that a previously placed study session does not
    # trigger a second 90-minute "class recovery" gap.  Fall back to all
    # blocked windows only when no obligation snapshot was supplied.
    obligation_source = obligation_blocked_windows if obligation_blocked_windows is not None else (blocked_windows or [])
    last_obligation_end = max((end for _, end in obligation_source), default=None)

    if last_obligation_end is None:
        recovery_end = EVENING_CORE_START_MINS
    else:
        if last_obligation_end <= 13 * 60:
            recovery_buffer = 45
        elif last_obligation_end <= 15 * 60:
            recovery_buffer = 60
        elif last_obligation_end <= 17 * 60:
            recovery_buffer = 75
        else:
            recovery_buffer = 90
        recovery_end = _round_up_minutes(last_obligation_end + recovery_buffer, 5)

    if last_obligation_end is None:
        comfortable_evening_end = 21 * 60
    elif last_obligation_end <= 13 * 60:
        comfortable_evening_end = 21 * 60
    elif last_obligation_end <= 15 * 60:
        comfortable_evening_end = 21 * 60 + 30
    elif last_obligation_end <= 17 * 60:
        comfortable_evening_end = 22 * 60
    elif last_obligation_end <= 19 * 60:
        comfortable_evening_end = 22 * 60 + 30
    else:
        comfortable_evening_end = DAY_END_MINS

    if total_day_study_mins is None or total_day_study_mins <= 0:
        target_start = max(EVENING_CORE_START_MINS, recovery_end)
    else:
        target_start = max(
            EVENING_START_MINS,
            recovery_end,
            comfortable_evening_end - total_day_study_mins,
        )

    target_start = max(target_start, EVENING_START_MINS)

    for free_start, free_end in evening_windows:
        if free_start <= target_start and free_end - target_start >= duration_mins:
            return target_start

    earliest_evening_start = min(start for start, _ in evening_windows)
    return max(target_start, earliest_evening_start)


def _find_slot_in_free_windows(
    free_windows: List[Tuple[int, int]],
    duration_mins: int,
    study_time_pref: str,
    blocked_windows: Optional[List[Tuple[int, int]]] = None,
    total_day_study_mins: Optional[int] = None,
    obligation_blocked_windows: Optional[List[Tuple[int, int]]] = None,
) -> Optional[Tuple[int, int]]:
    if not free_windows:
        return None

    pref = (study_time_pref or "").strip()

    if pref == "Night":
        target_start = _infer_evening_target_start(
            free_windows=free_windows,
            blocked_windows=blocked_windows,
            duration_mins=duration_mins,
            total_day_study_mins=total_day_study_mins,
            obligation_blocked_windows=obligation_blocked_windows,
        )
        if target_start is not None:
            night_slot = _pick_closest_slot_to_target(
                free_windows=free_windows,
                duration_mins=duration_mins,
                target_start_mins=target_start,
            )
            if night_slot:
                return night_slot

    if pref == "Morning":
        preferred_ranges = [(6 * 60, 12 * 60), (12 * 60, 18 * 60), (18 * 60, 23 * 60)]
        align = "start"
    elif pref == "Afternoon":
        preferred_ranges = [(12 * 60, 18 * 60), (18 * 60, 23 * 60), (6 * 60, 12 * 60)]
        align = "start"
    elif pref == "Night":
        preferred_ranges = [(18 * 60, 23 * 60), (12 * 60, 18 * 60), (6 * 60, 12 * 60)]
        align = "start"
    else:
        preferred_ranges = [(6 * 60, 23 * 60)]
        align = "start"

    for pref_start, pref_end in preferred_ranges:
        candidates: List[Tuple[int, int]] = []
        for free_start, free_end in free_windows:
            start = max(free_start, pref_start)
            end = min(free_end, pref_end)
            if end - start >= duration_mins:
                if align == "end":
                    candidates.append((end - duration_mins, end))
                else:
                    rounded_start = _round_up_minutes(start, 5)
                    if rounded_start + duration_mins <= end:
                        candidates.append((rounded_start, rounded_start + duration_mins))
                    else:
                        candidates.append((start, start + duration_mins))

        if candidates:
            if align == "end":
                return max(candidates, key=lambda x: x[0])
            return min(candidates, key=lambda x: x[0])

    # final fallback: anywhere in the free windows
    for free_start, free_end in free_windows:
        if free_end - free_start >= duration_mins:
            rounded_start = _round_up_minutes(free_start, 5)
            if rounded_start + duration_mins <= free_end:
                return (rounded_start, rounded_start + duration_mins)
            return (free_start, free_start + duration_mins)

    return None


def _build_module_name_lookup(user_modules: List[Dict], class_sessions: List[Dict]) -> Dict[str, str]:
    """
    Build a lookup that maps any form the AI might write for a module
    (module_code, module_name, 'code name', '[code] name', or any substring)
    → canonical module_name (from user_modules, falling back to extraction data).

    Matching priority (all case-insensitive):
      1. Exact module_name match
      2. Exact module_code match
      3. Subject string contains module_code
      4. Subject string contains a word-level match of module_name
    """
    # Build canonical map: code (upper) → name
    canonical: Dict[str, str] = {}  # code → name
    for m in (user_modules or []):
        code = (m.get("module_code") or "").strip().upper()
        name = (m.get("module_name") or "").strip()
        if code and name:
            canonical[code] = name

    # Supplement with extraction data (class_sessions may have codes not in user_modules)
    for s in (class_sessions or []):
        code = ((s.get("module_code") or s.get("module_alias") or "")).strip().upper()
        name = (s.get("module_name") or "").strip()
        if code and name and code not in canonical:
            canonical[code] = name

    return canonical


def _normalize_subject(raw_subject: str, module_lookup: Dict[str, str]) -> str:
    """
    Given whatever the AI wrote in the 'subject' field and the canonical
    code→name lookup, return the correct module_name.

    Falls back to the original raw_subject if nothing matches.
    """
    if not raw_subject or not module_lookup:
        return raw_subject

    subject = raw_subject.strip()
    subject_upper = subject.upper()

    # 1. Exact name match (case-insensitive)
    for code, name in module_lookup.items():
        if subject.lower() == name.lower():
            return name  # already correct, just normalise whitespace

    # 2. Exact code match (e.g. subject == "IT1522")
    if subject_upper in module_lookup:
        return module_lookup[subject_upper]

    # 3. Subject contains a known code (e.g. "[IT1522] Cybersecurity" or "IT1522 Cybersecurity")
    for code, name in module_lookup.items():
        if code in subject_upper:
            return name

    # 4. Subject contains a significant word from any known name
    #    (avoid very short words that could false-match)
    for code, name in module_lookup.items():
        name_words = [w for w in name.split() if len(w) >= 4]
        if name_words and all(w.lower() in subject.lower() for w in name_words):
            return name

    # No match — return original so we don't silently lose data
    return subject


def _build_blocked_windows(
    class_sessions: List[Dict],
    occupied_times: List[Dict],
) -> Dict[str, List[Tuple[int, int]]]:
    blocked: Dict[str, List[Tuple[int, int]]] = {day: [] for day in DAY_ORDER}

    for s in class_sessions or []:
        day = _normalize_day_name(s.get("day", ""))
        start = _time_to_minutes(s.get("start_time", ""))
        end = _time_to_minutes(s.get("end_time", ""))
        if day in blocked and start is not None and end is not None and start < end:
            blocked[day].append((start, end))

    for o in occupied_times or []:
        day = _normalize_day_name(o.get("day_of_week", ""))
        start = _time_to_minutes(o.get("start_time", ""))
        end = _time_to_minutes(o.get("end_time", ""))
        if day in blocked and start is not None and end is not None and start < end:
            blocked[day].append((start, end))

    return {day: _merge_intervals(windows) for day, windows in blocked.items()}


def _free_windows_for_day(
    blocked_windows: List[Tuple[int, int]],
    day_start: int = DAY_START_MINS,
    day_end: int = DAY_END_MINS,
    min_middle_gap_mins: int = MIN_MIDDLE_GAP_FOR_STUDY_MINS,
) -> List[Tuple[int, int]]:
    """
    Return realistic free windows for study.

    Rules:
    - before the first blocked period: allowed
    - after the last blocked period: allowed
    - between two blocked periods: allowed ONLY if the gap is large enough
      (default: at least 2 hours)
    """
    if not blocked_windows:
        return [(day_start, day_end)]

    normalized: List[Tuple[int, int]] = []
    for start, end in blocked_windows:
        if end <= day_start or start >= day_end:
            continue
        normalized.append((max(start, day_start), min(end, day_end)))

    normalized = _merge_intervals(normalized)
    if not normalized:
        return [(day_start, day_end)]

    free: List[Tuple[int, int]] = []

    # Before first blocked window
    first_start = normalized[0][0]
    if day_start < first_start:
        free.append((day_start, first_start))

    # Middle gaps: only allow if the gap is big enough
    for i in range(len(normalized) - 1):
        left_end = normalized[i][1]
        right_start = normalized[i + 1][0]
        gap = right_start - left_end

        if gap >= min_middle_gap_mins:
            free.append((left_end, right_start))

    # After last blocked window
    last_end = normalized[-1][1]
    if last_end < day_end:
        free.append((last_end, day_end))

    return free


def _block_lunch_on_class_day(
    blocked_windows: Dict[str, List[Tuple[int, int]]],
    class_sessions: List[Dict],
) -> None:
    """
    Block the lunch interval on days that have class sessions before and after
    the midday gap. This prevents unrealistic study sessions scheduled inside
    short lunch breaks or between-class gaps overlapping lunch.
    """
    sessions_by_day: Dict[str, List[Tuple[int, int]]] = {day: [] for day in DAY_ORDER}
    for s in class_sessions or []:
        day = _normalize_day_name(s.get("day", ""))
        start = _time_to_minutes(s.get("start_time", ""))
        end = _time_to_minutes(s.get("end_time", ""))
        if day in sessions_by_day and start is not None and end is not None and start < end:
            sessions_by_day[day].append((start, end))

    for day, sessions in sessions_by_day.items():
        if len(sessions) < 2:
            continue
        sessions.sort(key=lambda x: x[0])
        for i in range(len(sessions) - 1):
            current_end = sessions[i][1]
            next_start = sessions[i + 1][0]
            if current_end < next_start:
                gap_start = current_end
                gap_end = next_start
                if gap_end > LUNCH_START_MINS and gap_start < LUNCH_END_MINS:
                    blocked_windows.setdefault(day, []).append((LUNCH_START_MINS, LUNCH_END_MINS))
                    blocked_windows[day] = _merge_intervals(blocked_windows[day])
                    break


def _subtract_interval_from_windows(
    windows: List[Tuple[int, int]],
    remove_start: int,
    remove_end: int,
) -> List[Tuple[int, int]]:
    """
    Remove one interval from a list of free windows.
    Example:
      windows = [(06:00, 23:00)]
      remove  = (12:00, 13:30)
      result  = [(06:00, 12:00), (13:30, 23:00)]
    """
    result: List[Tuple[int, int]] = []

    for start, end in windows:
        if remove_end <= start or remove_start >= end:
            result.append((start, end))
            continue

        if start < remove_start:
            result.append((start, remove_start))
        if remove_end < end:
            result.append((remove_end, end))

    return [(s, e) for s, e in result if e > s]


def _append_study_as_block(
    blocked_map: Dict[str, List[Tuple[int, int]]],
    day: str,
    start_mins: int,
    end_mins: int,
    break_mins: int,
) -> None:
    """
    Mark a placed study session as blocked.
    Extends the blocked window by break_mins AFTER the session ends, so the
    next session placed on the same day cannot start during the break.
    Also extends break_mins BEFORE the session starts, so a session placed
    earlier on the same day cannot end during this session's lead-in break.
    """
    padded_start = max(DAY_START_MINS, start_mins - break_mins)
    padded_end = end_mins + break_mins
    blocked_map.setdefault(day, []).append((padded_start, padded_end))
    blocked_map[day] = _merge_intervals(blocked_map[day])


def enforce_planning_constraints(
    sessions: List[Dict],
    user_context: Dict,
    extraction_result,
) -> List[Dict]:
    prefs = user_context.get("preferences", {}) or {}
    occupied_times = user_context.get("occupied_times", []) or []

    extraction_data = extraction_result.model_dump() if hasattr(extraction_result, "model_dump") else {}
    class_sessions = extraction_data.get("class_sessions", []) or []

    allowed_days = _parse_allowed_study_days(prefs.get("study_days", "Mon,Tue,Wed,Thu,Fri"))
    preferred_study_time = prefs.get("preferred_study_time", "")
    break_pref_raw = prefs.get("break_preference", "medium")
    break_mins = BREAK_LENGTH_MINS.get(str(break_pref_raw).strip().lower(), 15)

    try:
        default_session_length = int(str(prefs.get("session_length", "60")).strip())
    except Exception:
        default_session_length = 60

    # Minimum valid duration: anything shorter than half the user's chosen
    # session length is treated as a malformed slot and replaced with the
    # user's configured session length.
    min_valid_duration = max(15, default_session_length // 2)

    blocked_map = _build_blocked_windows(class_sessions, occupied_times)
    _block_lunch_on_class_day(blocked_map, class_sessions)
    # Snapshot the obligation-only blocked map (class sessions + commitments)
    # before any study sessions are added.  This is passed to
    # _infer_evening_target_start so the class-recovery buffer only fires
    # after real obligations, not after previously placed study sessions.
    base_obligation_map: Dict[str, List[Tuple[int, int]]] = copy.deepcopy(blocked_map)

    # -- Module name normalisation lookup ------------------------------------------
    # The AI may write the subject as a code, a name, or a code+name mix.
    # Build a canonical code->name map so we always store module_name only.
    user_modules_list = user_context.get('modules', []) or []
    module_lookup = _build_module_name_lookup(user_modules_list, class_sessions)

    # Build a quick lookup to know whether each day has any class at all
    class_days_with_sessions = set()
    for cs in class_sessions:
        day = _normalize_day_name(cs.get("day", ""))
        if day in DAY_ORDER:
            class_days_with_sessions.add(day)

    normalized_sessions: List[Dict] = []
    for s in sessions or []:
        day = _normalize_day_name(s.get("day", ""))
        if day not in DAY_ORDER:
            continue
        if day not in allowed_days:
            continue

        start = _time_to_minutes(s.get("start", ""))
        end = _time_to_minutes(s.get("end", ""))

        if start is None or end is None or end <= start:
            orig_start = None
        else:
            orig_start = start

        # Enforce exact configured session length for all regular study sessions.
        duration = default_session_length

        raw_subject = s.get("subject", "")
        normalized_subject = _normalize_subject(raw_subject, module_lookup)
        normalized_sessions.append({
            "day": day,
            "subject": normalized_subject,
            "topic": s.get("topic", ""),
            "type": "study",
            "_orig_start_mins": orig_start,
            "_duration_mins": duration,
        })

    normalized_sessions.sort(
        key=lambda x: (
            DAY_INDEX.get(x["day"], 999),
            x["_orig_start_mins"] if x["_orig_start_mins"] is not None else 9999,
            x.get("subject", ""),
        )
    )

    day_total_study_mins: Dict[str, int] = {day: 0 for day in DAY_ORDER}
    day_session_counts: Dict[str, int] = {day: 0 for day in DAY_ORDER}
    for s in normalized_sessions:
        day = s["day"]
        day_total_study_mins[day] += s["_duration_mins"]
        day_session_counts[day] += 1

    day_total_with_breaks: Dict[str, int] = {}
    for day in DAY_ORDER:
        count = day_session_counts[day]
        extra_breaks = max(0, count - 1) * break_mins
        day_total_with_breaks[day] = day_total_study_mins[day] + extra_breaks

    final_sessions: List[Dict] = []

    for s in normalized_sessions:
        day = s["day"]
        duration = s["_duration_mins"]

        current_blocked = blocked_map.get(day, [])
        # Use the actual session+break size as the minimum viable middle gap.
        # This is smaller than MIN_MIDDLE_GAP_FOR_STUDY_MINS on purpose:
        # _free_windows_for_day already filtered out short between-class gaps
        # when building blocked_map from class_sessions.  Here we only want to
        # exclude gaps that literally cannot fit one session.
        free_windows = _free_windows_for_day(
            blocked_windows=current_blocked,
            day_start=DAY_START_MINS,
            day_end=DAY_END_MINS,
            min_middle_gap_mins=duration + break_mins,
        )

        # NEW RULE:
        # On no-class days, protect lunch so morning preference does not push lunch too late.
        # Example: do not place 12:15–13:15 on a free day.
        has_class_that_day = day in class_days_with_sessions
        if not has_class_that_day:
            free_windows = _subtract_interval_from_windows(
                free_windows,
                LUNCH_START_MINS,
                LUNCH_END_MINS,
            )

        realistic_free_windows = []
        # Minimum gap needed to fit one study session plus its surrounding break.
        min_gap_for_session = duration + break_mins
        for idx, (free_start, free_end) in enumerate(free_windows):
            gap_len = free_end - free_start

            is_first_window = (idx == 0)
            is_last_window = (idx == len(free_windows) - 1)

            if is_first_window or is_last_window:
                if gap_len >= duration:
                    realistic_free_windows.append((free_start, free_end))
                continue

            # For middle gaps: must fit the session plus at least one break on
            # each side.  Use min_gap_for_session so short study-session gaps
            # are still usable, while very short between-class gaps are not.
            if gap_len >= max(min_gap_for_session, duration):
                realistic_free_windows.append((free_start, free_end))

        slot = _find_slot_in_free_windows(
            free_windows=realistic_free_windows,
            duration_mins=duration,
            study_time_pref=preferred_study_time,
            blocked_windows=current_blocked,
            total_day_study_mins=day_total_with_breaks.get(day),
            obligation_blocked_windows=base_obligation_map.get(day, []),
        )

        if not slot:
            # Fallback: try placing on another allowed day that has an available realistic free slot
            for fallback_day in allowed_days:
                if fallback_day == day:
                    continue
                fb_blocked = blocked_map.get(fallback_day, [])
                fb_free = _free_windows_for_day(
                    blocked_windows=fb_blocked,
                    day_start=DAY_START_MINS,
                    day_end=DAY_END_MINS,
                    min_middle_gap_mins=duration + break_mins,
                )
                if fallback_day not in class_days_with_sessions:
                    fb_free = _subtract_interval_from_windows(
                        fb_free,
                        LUNCH_START_MINS,
                        LUNCH_END_MINS,
                    )
                fb_realistic = []
                for idx, (free_start, free_end) in enumerate(fb_free):
                    gap_len = free_end - free_start
                    if idx == 0 or idx == len(fb_free) - 1:
                        if gap_len >= duration:
                            fb_realistic.append((free_start, free_end))
                        continue
                    if gap_len >= max(duration + break_mins, duration):
                        fb_realistic.append((free_start, free_end))

                fb_slot = _find_slot_in_free_windows(
                    free_windows=fb_realistic,
                    duration_mins=duration,
                    study_time_pref=preferred_study_time,
                    blocked_windows=fb_blocked,
                    total_day_study_mins=day_total_with_breaks.get(fallback_day),
                    obligation_blocked_windows=base_obligation_map.get(fallback_day, []),
                )
                if fb_slot:
                    day = fallback_day
                    slot = fb_slot
                    break

        if not slot:
            continue

        start_mins, end_mins = slot

        final_sessions.append({
            "day": day,
            "start": _minutes_to_hhmm(start_mins),
            "end": _minutes_to_hhmm(end_mins),
            "subject": s.get("subject", ""),
            "topic": s.get("topic", ""),
            "type": "study",
        })

        _append_study_as_block(
            blocked_map=blocked_map,
            day=day,
            start_mins=start_mins,
            end_mins=end_mins,
            break_mins=break_mins,
        )

    final_sessions.sort(
        key=lambda x: (
            DAY_INDEX.get(x["day"], 999),
            _time_to_minutes(x["start"]) or 9999,
            x.get("subject", ""),
        )
    )
    return final_sessions

# ─────────────────────────────────────────────────────────────────────────────
# Extraction data formatters
# ─────────────────────────────────────────────────────────────────────────────

def _format_class_sessions(sessions: List[Dict]) -> str:
    if not sessions:
        return "  (No class sessions extracted)"

    by_day: Dict[str, List[Dict]] = {d: [] for d in DAY_ORDER}
    for s in sessions:
        day = _normalize_day_name(s.get("day", ""))
        if day in by_day:
            by_day[day].append(s)

    lines: List[str] = []
    for day in DAY_ORDER:
        day_sessions = sorted(by_day[day], key=lambda x: x.get("start_time", ""))
        if not day_sessions:
            continue
        lines.append(f"\n  {day}:")
        for s in day_sessions:
            code = s.get("module_code", "") or s.get("module_alias", "")
            name = s.get("module_name", "")
            start = s.get("start_time", "?")
            end = s.get("end_time", "?")
            stype = s.get("session_type", "")
            pattern = s.get("week_pattern", "")
            loc = s.get("location", "")
            line = f"    • {start}–{end}  [{code}] {name}"
            if stype:
                line += f"  ({stype})"
            if pattern:
                line += f"  {pattern}"
            if loc:
                line += f"  @ {loc}"
            lines.append(line)

    return "\n".join(lines) if lines else "  (No sessions found)"


def _format_assessments(assessments: List[Dict]) -> str:
    if not assessments:
        return "  (No assessments extracted)"

    lines: List[str] = []
    for a in assessments:
        code = a.get("module_code", "")
        title = a.get("title", "")
        week = a.get("week_number", "")
        weight = a.get("weightage", "")
        atype = a.get("assessment_type", "")
        scope = a.get("topic_scope", "")
        dur = a.get("duration", "")
        entry = f"  • Wk {week}  [{code}] {title}"
        if atype:
            entry += f"  — {atype}"
        if weight:
            entry += f"  ({weight})"
        if scope:
            entry += f"  | Topic: {scope}"
        if dur:
            entry += f"  | Duration: {dur}"
        lines.append(entry)
    return "\n".join(lines)


def _format_module_schedule(schedule: List[Dict]) -> str:
    if not schedule:
        return "  (No module schedule extracted)"

    lines: List[str] = []
    last_code = None
    for entry in schedule:
        code = entry.get("module_code", "")
        week = entry.get("week_number", "")
        activities = entry.get("activities", "")
        hours = entry.get("hours", "")
        mode = entry.get("mode", "")
        if code != last_code:
            lines.append(f"\n  [{code}]")
            last_code = code
        row = f"    Wk {week}: {activities}"
        extras = []
        if hours:
            extras.append(f"{hours}h")
        if mode:
            extras.append(mode)
        if extras:
            row += f"  ({', '.join(extras)})"
        lines.append(row)
    return "\n".join(lines) if lines else "  (No schedule found)"


def _format_current_week_module_schedule(schedule: List[Dict], current_week_number: Optional[int]) -> str:
    if not schedule:
        return "  (No module schedule extracted)"

    if current_week_number is None:
        return _format_module_schedule(schedule)

    filtered = []
    for entry in schedule:
        wk = _safe_int(entry.get("week_number", ""))
        if wk == current_week_number:
            filtered.append(entry)

    if not filtered:
        return f"  (No module schedule found for Week {current_week_number})"

    lines: List[str] = [f"  Current academic week: Week {current_week_number}"]
    last_code = None

    for entry in filtered:
        code = entry.get("module_code", "")
        week = entry.get("week_number", "")
        activities = entry.get("activities", "")
        hours = entry.get("hours", "")
        mode = entry.get("mode", "")

        if code != last_code:
            lines.append(f"\n  [{code}]")
            last_code = code

        row = f"    Wk {week}: {activities}"
        extras = []
        if hours:
            extras.append(f"{hours}h")
        if mode:
            extras.append(mode)
        if extras:
            row += f"  ({', '.join(extras)})"
        lines.append(row)

    return "\n".join(lines)


def _format_upcoming_assessments(
    assessments: List[Dict],
    current_week_number: Optional[int],
    lookahead: int = 2,
) -> str:
    """
    Format upcoming assessments within a lookahead window.
    Default lookahead is 2 weeks (current + next 2).
    Term-break weeks pass lookahead=3 for wider visibility.
    """
    if not assessments:
        return "  (No assessments extracted)"

    if current_week_number is None:
        return _format_assessments(assessments)

    end_week = current_week_number + lookahead
    filtered = []
    for a in assessments:
        wk = _safe_int(a.get("week_number", ""))
        if wk is not None and current_week_number <= wk <= end_week:
            filtered.append(a)

    if not filtered:
        return f"  (No assessments found from Week {current_week_number} to Week {end_week})"

    lines: List[str] = [f"  Relevant assessments from Week {current_week_number} to Week {end_week}:"]
    for a in filtered:
        code   = a.get("module_code", "")
        title  = a.get("title", "")
        week   = a.get("week_number", "")
        weight = a.get("weightage", "")
        atype  = a.get("assessment_type", "")
        scope  = a.get("topic_scope", "")
        dur    = a.get("duration", "")

        entry = f"  • Wk {week}  [{code}] {title}"
        if atype:
            entry += f"  — {atype}"
        if weight:
            entry += f"  ({weight})"
        if scope:
            entry += f"  | Topic: {scope}"
        if dur:
            entry += f"  | Duration: {dur}"
        lines.append(entry)

    return "\n".join(lines)


def _format_occupied_times(occupied: List[Dict]) -> str:
    if not occupied:
        return "  (None)"
    lines: List[str] = []
    for o in occupied:
        title = o.get("title", "")
        category = o.get("category", "")
        day = o.get("day_of_week", "")
        start = o.get("start_time", "")
        end = o.get("end_time", "")
        notes = o.get("notes", "")
        entry = f"  • {day} {start}–{end}  {title}"
        if category:
            entry += f"  [{category}]"
        if notes:
            entry += f"  — {notes}"
        lines.append(entry)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Weekly class-priority map
# ─────────────────────────────────────────────────────────────────────────────

def build_class_priority_text(user_context: Dict, extraction_result) -> str:
    if extraction_result is None:
        return "  (No extraction data — cannot build class-priority map yet)"

    data = extraction_result.model_dump() if hasattr(extraction_result, "model_dump") else {}
    sessions = data.get("class_sessions", []) or []

    prefs = user_context.get("preferences", {}) or {}
    allowed_study_days = _parse_allowed_study_days(prefs.get("study_days", "Mon,Tue,Wed,Thu,Fri"))

    class_day_modules: Dict[str, List[Dict]] = {day: [] for day in DAY_ORDER}
    seen_keys = set()

    for s in sessions:
        class_day = _normalize_day_name(s.get("day", ""))
        if class_day not in class_day_modules:
            continue

        code = (s.get("module_code") or s.get("module_alias") or "").strip()
        name = (s.get("module_name") or "").strip()
        session_type = (s.get("session_type") or "").strip()

        dedupe_key = (class_day, code, name)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        class_day_modules[class_day].append({
            "module_code": code,
            "module_name": name,
            "session_type": session_type,
        })

    lines = [
        "  Use this weekly backward-planning map as a STRICT priority guide:",
        "  - Tuesday classes should be prepared on Monday.",
        "  - Wednesday classes should be prepared on Tuesday.",
        "  - Thursday classes should be prepared on Wednesday.",
        "  - Friday classes should be prepared on Thursday.",
        "  - Monday classes should be prepared on the previous Sunday; if Sunday is not an allowed study day, use Saturday.",
        "  - If neither Sunday nor Saturday is allowed, use the nearest earlier allowed study day.",
        "  - These are regular study-priority sessions, not just tiny reminder slots.",
        "",
        "  Generated priority map from the student's actual class timetable:",
    ]

    has_any = False
    for class_day in DAY_ORDER:
        modules = class_day_modules[class_day]
        if not modules:
            continue

        prep_day = _pick_prep_day(class_day, allowed_study_days)
        module_labels = []
        for m in modules:
            code = m.get("module_code", "")
            name = m.get("module_name", "")
            stype = m.get("session_type", "")
            label = f"[{code}] {name}".strip()
            if stype:
                label += f" ({stype})"
            module_labels.append(label)

        if prep_day:
            lines.append(f"  • {class_day} classes → prioritise on {prep_day}: {', '.join(module_labels)}")
        else:
            lines.append(f"  • {class_day} classes → no allowed earlier study day found: {', '.join(module_labels)}")
        has_any = True

    return "\n".join(lines) if has_any else "  (No class sessions found — cannot build class-priority map)"


# ─────────────────────────────────────────────────────────────────────────────
# Extraction result → formatted text blocks
# ─────────────────────────────────────────────────────────────────────────────

def _format_week_zero_module_preview(schedule: List[Dict], user_modules: List[Dict]) -> str:
    lines: List[str] = [
        "  Week 0 focus: onboarding + light preview of Week 1 only.",
        "  Use this as orientation material, not as a full deep-study plan.",
    ]

    schedule_by_code: Dict[str, List[Dict]] = {}
    for entry in schedule or []:
        code = (entry.get("module_code") or "").strip()
        wk = _safe_int(entry.get("week_number", ""))
        if not code or wk != 1:
            continue
        schedule_by_code.setdefault(code, []).append(entry)

    if not user_modules:
        user_modules = []
        for code, entries in schedule_by_code.items():
            first = entries[0] if entries else {}
            user_modules.append({
                "module_code": code,
                "module_name": first.get("module_name", "") or code,
            })

    if not user_modules and not schedule_by_code:
        return "  (No module data found for Week 0 onboarding preview)"

    for module in user_modules:
        code = (module.get("module_code") or "").strip()
        name = (module.get("module_name") or "").strip()
        if code or name:
            lines.append(f"\n  [{code}] {name}".rstrip())
        else:
            lines.append("\n  [Module]")

        lines.append(
            "    • Onboarding: understand what this module is about, expected learning style, and any tools/software/platforms needed."
        )

        week_one_entries = schedule_by_code.get(code, [])
        if week_one_entries:
            for entry in week_one_entries:
                activities = (entry.get("activities") or "").strip()
                hours = (entry.get("hours") or "").strip()
                mode = (entry.get("mode") or "").strip()
                row = f"    • Light preview of Week 1: {activities or 'skim the first lesson topic and key terms'}"
                extras = []
                if hours:
                    extras.append(f"{hours}h")
                if mode:
                    extras.append(mode)
                if extras:
                    row += f"  ({', '.join(extras)})"
                lines.append(row)
            lines.append(
                "    • Week 0 outcome: know the first topic at a high level, prepare 2–3 class questions, and arrive feeling oriented."
            )
        else:
            lines.append(
                "    • Light preview of Week 1: skim the first topic, learn the key terms, and note 2–3 questions to ask in class."
            )

    return "\n".join(lines)


def _format_term_break_revision_context(
    schedule: List[Dict],
    assessments: List[Dict],
    current_week_number: Optional[int],
) -> str:
    """
    Build the 'current week topics' block for a term-break week.
    Instead of showing this week's class activities, we show:
    - All modules that have assessments in the next 3 weeks (catch-up urgency)
    - A short look-back at the last 2 weeks of activities per module (revision context)
    """
    lines: List[str] = [
        f"  TERM-BREAK WEEK (Week {current_week_number})",
        "  No regular classes this week — use this time for revision, catch-up, and assessment prep.",
        "",
        "  ── UPCOMING ASSESSMENTS (next 3 weeks) ──",
    ]

    if assessments and current_week_number is not None:
        upcoming = [
            a for a in assessments
            if _safe_int(a.get("week_number", "")) is not None
            and current_week_number <= _safe_int(a.get("week_number", "")) <= current_week_number + 3
        ]
        if upcoming:
            for a in upcoming:
                code  = a.get("module_code", "")
                title = a.get("title", "")
                wk    = a.get("week_number", "")
                wt    = a.get("weightage", "")
                atype = a.get("assessment_type", "")
                line  = f"  • Wk {wk}  [{code}] {title}"
                if atype:
                    line += f"  — {atype}"
                if wt:
                    line += f"  ({wt})"
                lines.append(line)
        else:
            lines.append("  (No assessments found in the next 3 weeks)")
    else:
        lines.append("  (No assessment data available)")

    lines.append("")
    lines.append("  ── RECENT TOPICS TO REVISE (last 2 weeks per module) ──")

    if schedule and current_week_number is not None:
        look_back_weeks = [current_week_number - 1, current_week_number - 2]
        look_back_weeks = [w for w in look_back_weeks if w >= 1]

        by_code: Dict[str, List[Dict]] = {}
        for entry in schedule:
            wk = _safe_int(entry.get("week_number", ""))
            if wk not in look_back_weeks:
                continue
            code = (entry.get("module_code") or "").strip()
            by_code.setdefault(code, []).append(entry)

        if by_code:
            for code, entries in sorted(by_code.items()):
                module_name = (entries[0].get("module_name") or "").strip()
                lines.append(f"\n  [{code}] {module_name}".rstrip())
                for entry in entries:
                    wk         = entry.get("week_number", "")
                    activities = (entry.get("activities") or "").strip()
                    hours      = (entry.get("hours") or "").strip()
                    mode       = (entry.get("mode") or "").strip()
                    row = f"    Wk {wk}: {activities}"
                    extras = []
                    if hours:
                        extras.append(f"{hours}h")
                    if mode:
                        extras.append(mode)
                    if extras:
                        row += f"  ({', '.join(extras)})"
                    lines.append(row)
        else:
            lines.append("  (No recent module schedule data found for look-back)")
    else:
        lines.append("  (No module schedule data available)")

    return "\n".join(lines)


def build_term_break_planning_focus_text(
    current_week_number: Optional[int],
    term_break_weeks: Optional[List[int]] = None,
) -> str:
    """
    Return the PLANNING MODE section text injected into the prompt
    when the target week is a term break.
    """
    wk_label = f"Week {current_week_number}" if current_week_number is not None else "this week"
    break_list = ""
    if term_break_weeks:
        break_list = f"  Term-break weeks detected from timetable: {', '.join(f'Week {w}' for w in term_break_weeks)}\n"

    return (
        f"TERM-BREAK MODE — {wk_label} is a term/semester break week.\n"
        f"{break_list}"
        "  There are NO regular classes this week. Do NOT apply the class-driven pre-study flow.\n"
        "\n"
        "  GOALS FOR THIS WEEK:\n"
        "  1. REVISION — revisit and consolidate topics from recent weeks that were hard or important.\n"
        "  2. CATCH-UP — complete any work that fell behind during the regular school weeks.\n"
        "  3. ASSESSMENT READINESS — prioritise modules with upcoming tests, assignments, labs, or exams.\n"
        "\n"
        "  HOW TO PLAN TERM-BREAK SESSIONS:\n"
        "  - Distribute sessions across ALL modules, weighted by upcoming assessment urgency.\n"
        "  - Modules with the nearest upcoming assessment get the most sessions.\n"
        "  - Modules with high-weightage assessments get priority over low-weightage ones.\n"
        "  - Each session topic should be a specific revision or catch-up task, e.g.:\n"
        '    "Revise Week 8 database normalisation — 1NF to 3NF", or\n'
        '    "Catch up on Week 9 lab: implement linked list from scratch", or\n'
        '    "Practice Test 1 topics: SQL joins and subqueries (Wk 12 test prep)"\n'
        "  - Do NOT use vague topics like 'general revision' or 'catch up on notes'.\n"
        "  - Use the UPCOMING ASSESSMENTS section and RECENT TOPICS section above to choose specific topics.\n"
        "\n"
        "  SCHEDULING RULES FOR TERM BREAK:\n"
        "  - The student still has recurring commitments (occupied times) to respect.\n"
        "  - There are no class blocks to avoid this week (it is a break week).\n"
        "  - The student's preferred study time, session length, and intensity still apply.\n"
        "  - Spread sessions across the week — do NOT front-load them all on Monday.\n"
        "  - Respect the weekly hour cap from the student's intensity setting.\n"
        "  - This is a RECOVERY and CONSOLIDATION week — avoid overloading the student."
    )


def build_week_planning_focus_text(
    target_week_number: Optional[int],
    term_break_weeks: Optional[List[int]] = None,
) -> str:
    """
    Return the planning-mode instruction block for the given week.
    Handles three distinct modes:
      - Week 0  : onboarding / light-preview mode
      - Term break: revision + catch-up + assessment readiness mode
      - Normal  : class-driven pre-study mode (default)
    """
    if target_week_number == 0:
        return (
            "WEEK 0 MODE — This is a special onboarding week before the normal school-week flow.\n"
            "- Focus on module onboarding, setup, and confidence-building.\n"
            "- For each module, help the student understand what the module is about, what tools/software may be needed, and what the first important topics are.\n"
            "- Use Week 1 topics only for a LIGHT preview: skim Topic 1, learn key terms, understand the likely class objective, prepare 2–3 questions, or watch one short intro resource.\n"
            "- Do NOT duplicate the normal Week 1 deep pre-study flow.\n"
            "- Do NOT make Week 0 sessions sound like full Tuesday-for-Monday or Wednesday-for-Tuesday class preparation.\n"
            "- Session topics for Week 0 should explicitly sound like onboarding / preview / orientation / warm-up, not full mastery.\n"
            "- Keep Week 0 balanced across modules so the student starts the semester feeling prepared, not overloaded."
        )

    if term_break_weeks and target_week_number in term_break_weeks:
        return build_term_break_planning_focus_text(
            current_week_number=target_week_number,
            term_break_weeks=term_break_weeks,
        )

    return (
        "NORMAL SCHOOL-WEEK MODE — Keep the existing class-first pre-study flow.\n"
        "- Use the current week as the main academic target.\n"
        "- Tuesday classes should drive Monday prep, Wednesday classes should drive Tuesday prep, and so on.\n"
        "- Monday classes should be prepared on the previous weekend or nearest earlier allowed study day.\n"
        "- Study sessions should be meaningful preparation, not generic reminders."
    )


def format_extraction_for_prompt(
    extraction_result,
    current_week_number: Optional[int] = None,
    user_modules: Optional[List[Dict]] = None,
) -> Dict[str, str]:
    if extraction_result is None:
        placeholder = "  (No extraction data — upload files and run extraction first)"
        return {
            "class_sessions_text": placeholder,
            "assessments_text": placeholder,
            "module_schedule_text": placeholder,
            "current_week_module_schedule_text": placeholder,
            "upcoming_assessments_text": placeholder,
            "week_planning_focus_text": build_week_planning_focus_text(current_week_number),
            "is_term_break": False,
        }

    data = extraction_result.model_dump() if hasattr(extraction_result, "model_dump") else {}
    module_schedule = data.get("module_schedule", [])
    assessments = data.get("assessments", [])

    # ── Detect term-break ────────────────────────────────────────────────────
    term_break_weeks = get_term_break_weeks(extraction_result)
    week_is_term_break = (
        current_week_number is not None
        and current_week_number in term_break_weeks
    )

    if current_week_number == 0:
        current_week_module_schedule_text = _format_week_zero_module_preview(
            module_schedule,
            user_modules or [],
        )
        upcoming_assessments_text = _format_upcoming_assessments(assessments, 1)

    elif week_is_term_break:
        # For a term-break week, replace the "current week topics" block with
        # a consolidation-focused revision + upcoming-assessment context block.
        current_week_module_schedule_text = _format_term_break_revision_context(
            schedule=module_schedule,
            assessments=assessments,
            current_week_number=current_week_number,
        )
        # Show assessments over a longer 3-week horizon during break
        upcoming_assessments_text = _format_upcoming_assessments(
            assessments,
            current_week_number,
            lookahead=3,
        )

    else:
        current_week_module_schedule_text = _format_current_week_module_schedule(
            module_schedule,
            current_week_number,
        )
        upcoming_assessments_text = _format_upcoming_assessments(
            assessments,
            current_week_number,
        )

    focus_text = build_week_planning_focus_text(
        target_week_number=current_week_number,
        term_break_weeks=term_break_weeks if term_break_weeks else None,
    )

    return {
        "class_sessions_text": _format_class_sessions(data.get("class_sessions", [])),
        "assessments_text": _format_assessments(assessments),
        "module_schedule_text": _format_module_schedule(module_schedule),
        "current_week_module_schedule_text": current_week_module_schedule_text,
        "upcoming_assessments_text": upcoming_assessments_text,
        "week_planning_focus_text": focus_text,
        "is_term_break": week_is_term_break,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_study_plan_prompt(
    user_context: Dict,
    class_sessions_text: str,
    assessments_text: str,
    module_schedule_text: str,
    current_week_module_schedule_text: str,
    upcoming_assessments_text: str,
    class_priority_text: str,
    week_planning_focus_text: str = "",
    today_date: Optional[str] = None,
    target_week_number: Optional[int] = None,
) -> str:
    prefs = user_context.get("preferences", {}) or {}
    modules = user_context.get("modules", []) or []
    occupied = user_context.get("occupied_times", []) or []

    session_len_raw = prefs.get("session_length", "60")
    break_pref_raw = prefs.get("break_preference", "medium")
    study_time_pref = prefs.get("preferred_study_time", "")
    study_days_raw = prefs.get("study_days", "Mon,Tue,Wed,Thu,Fri")
    sem_start = prefs.get("semester_start_date", "")
    sem_end = prefs.get("semester_end_date", "")

    current_week_number = target_week_number if target_week_number is not None else compute_academic_week(sem_start, today_date)

    study_days_full = [
        DAY_ABBREV_MAP.get(d.strip(), d.strip())
        for d in study_days_raw.split(",") if d.strip()
    ]

    intensity_key = str(prefs.get("study_intensity", "balanced")).strip().lower()
    intensity_label, hrs_min, hrs_max = INTENSITY_HOURS.get(intensity_key, ("Steady Progress", 16, 25))
    session_label = SESSION_LENGTH_LABELS.get(session_len_raw, f"{session_len_raw} minutes")
    break_label = BREAK_LENGTH_LABELS.get(break_pref_raw, "10–15 minutes")
    break_mins_for_prompt = BREAK_LENGTH_MINS.get(str(break_pref_raw).strip().lower(), 15)
    time_window_rule = build_study_time_window_rule(study_time_pref)

    try:
        session_mins = int(session_len_raw)
    except ValueError:
        session_mins = 60

    target_min_mins = hrs_min * 60
    target_max_mins = hrs_max * 60
    min_sessions = max(1, target_min_mins // session_mins)
    max_sessions = max(min_sessions, target_max_mins // session_mins)

    module_lines = "\n".join(
        f"  • {m.get('module_code', '')} — {m.get('module_name', '')}"
        for m in modules
    ) or "  (No modules saved)"

    template = load_planning_prompt()
    return template.format_map({
        "today_line": today_date or "Unknown",
        "module_lines": module_lines,
        "sem_start": sem_start,
        "sem_end": sem_end,
        "current_week_number": current_week_number if current_week_number is not None else "Unknown",
        "class_sessions_text": class_sessions_text,
        "module_schedule_text": module_schedule_text,
        "current_week_module_schedule_text": current_week_module_schedule_text,
        "assessments_text": assessments_text,
        "upcoming_assessments_text": upcoming_assessments_text,
        "occupied_times_text": _format_occupied_times(occupied),
        "class_priority_text": class_priority_text,
        "week_planning_focus_text": week_planning_focus_text or build_week_planning_focus_text(current_week_number),
        "intensity_label": intensity_label,
        "hrs_min": hrs_min,
        "hrs_max": hrs_max,
        "target_min_mins": target_min_mins,
        "target_max_mins": target_max_mins,
        "hrs_max_mins": target_max_mins,
        "session_label": session_label,
        "break_label": break_label,
        "break_mins": break_mins_for_prompt,
        "study_days_str": ", ".join(study_days_full),
        "study_time_pref": study_time_pref or "No preference",
        "time_window_rule": time_window_rule,
        "session_mins": session_mins,
        "min_sessions": min_sessions,
        "max_sessions": max_sessions,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Output parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    return re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", text, flags=re.IGNORECASE).strip()


def _find_balanced_json(text: str, opener: str, closer: str) -> str:
    start = text.find(opener)
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]
        start = text.find(opener, start + 1)
    return ""


def _extract_json_candidate(full_text: str) -> str:
    text = (full_text or "").strip()
    if not text:
        return ""

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        inner = fenced.group(1).strip()
        if inner.startswith("[") or inner.startswith("{"):
            return inner

    array_candidate = _find_balanced_json(text, "[", "]")
    if array_candidate:
        return array_candidate.strip()

    object_candidate = _find_balanced_json(text, "{", "}")
    if object_candidate:
        return object_candidate.strip()

    return ""


def parse_and_validate_sessions(full_text: str) -> List[Dict]:
    try:
        candidate = _extract_json_candidate(full_text)
        if not candidate:
            return []

        parsed = _json.loads(candidate)
        raw_sessions = parsed.get("sessions", []) if isinstance(parsed, dict) else parsed
        if not isinstance(raw_sessions, list):
            return []

        result = StudyPlanResult(sessions=raw_sessions)
        return [s.model_dump() for s in result.sessions]
    except Exception:
        return []


def extract_plan_sections(full_text: str) -> Dict[str, str]:
    text_without_json = _strip_code_fences(full_text)

    candidate = _extract_json_candidate(text_without_json)
    if candidate:
        text_without_json = text_without_json.replace(candidate, " ", 1).strip()

    section_patterns = {
        "summary": r"(?:📊\s*)?WEEKLY STUDY SUMMARY",
        "tips": r"(?:💡\s*)?STUDY TIPS(?: FOR THIS STUDENT)?",
        "alerts": r"(?:⚠️\s*)?DEADLINE ALERTS",
    }

    matches = []
    for key, pattern in section_patterns.items():
        m = re.search(pattern, text_without_json, re.IGNORECASE)
        if m:
            matches.append((m.start(), m.end(), key))

    matches.sort(key=lambda x: x[0])
    found: Dict[str, str] = {}

    for i, (_, end, key) in enumerate(matches):
        body_end = matches[i + 1][0] if i + 1 < len(matches) else len(text_without_json)
        body = text_without_json[end:body_end].strip(" \n\r\t━-")
        found[key] = body.strip()

    return {
        "summary": found.get("summary", ""),
        "tips": found.get("tips", ""),
        "alerts": found.get("alerts", ""),
    }


def _extract_repaired_payload(raw_text: str) -> Dict:
    candidate = _extract_json_candidate(raw_text)
    if not candidate:
        return {}
    try:
        parsed = _json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {"sessions": parsed}
    except Exception:
        return {}


def _call_bedrock_text(prompt: str, system_prompt: str, max_tokens: int = 8000) -> str:
    import boto3 as _boto3
    from config import Config

    session = _boto3.Session(**Config.bedrock_session_kwargs())
    bedrock = session.client("bedrock-runtime", region_name=Config.AWS_REGION)
    model_id = Config.resolve_bedrock_model_id(None)

    response = bedrock.converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": 0,
        },
    )

    output = response.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])
    return "\n".join(block.get("text", "") for block in content if block.get("text")).strip()


def repair_plan_output(full_text: str) -> Dict[str, object]:
    repair_prompt = f"""
The following study-plan output was malformed because it included reasoning or the wrong format.
Convert it into the strict JSON schema described in the system instruction.

SOURCE OUTPUT:
{full_text}
""".strip()

    repaired_text = _call_bedrock_text(
        prompt=repair_prompt,
        system_prompt=REPAIR_SYSTEM_PROMPT,
        max_tokens=8000,
    )
    return _extract_repaired_payload(repaired_text)


# ─────────────────────────────────────────────────────────────────────────────
# Bedrock streaming
# ─────────────────────────────────────────────────────────────────────────────

def stream_study_plan(prompt: str, user_context: Optional[Dict] = None, extraction_result=None):
    import boto3 as _boto3
    from config import Config

    try:
        session_kwargs = Config.bedrock_session_kwargs()
        print(f"[Planning Agent] Bedrock session kwargs: {list(session_kwargs.keys())}")

        boto_session = _boto3.Session(**session_kwargs)
        bedrock = boto_session.client("bedrock-runtime", region_name=Config.AWS_REGION)
        model_id = Config.resolve_bedrock_model_id(None)

        print(f"[Planning Agent] Using model: {model_id}")
        print(f"[Planning Agent] Region: {Config.AWS_REGION}")
    except Exception as exc:
        error_msg = f"Failed to initialize AWS Bedrock client: {str(exc)}"
        print(f"[Planning Agent] ERROR: {error_msg}")
        yield {"error": error_msg}
        return

    try:
        print(f"[Planning Agent] Starting converse_stream with model {model_id}")
        stream_resp = bedrock.converse_stream(
            modelId=model_id,
            system=[{"text": PLANNING_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": 8000,
                "temperature": 0,
            },
        )
        print(f"[Planning Agent] Stream initiated successfully")
    except Exception as exc:
        error_msg = f"Bedrock API call failed: {str(exc)}"
        print(f"[Planning Agent] ERROR: {error_msg}")
        yield {"error": error_msg}
        return

    full_chunks: List[str] = []

    try:
        for event in stream_resp.get("stream", []):
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                text = delta.get("text", "")
                if text:
                    full_chunks.append(text)
                    yield {"text": text}

            elif "messageStop" in event:
                full_text = "".join(full_chunks).strip()
                timetable_sessions = parse_and_validate_sessions(full_text)
                sections = extract_plan_sections(full_text)

                needs_repair = (
                    not timetable_sessions
                    or not any((sections.get("summary"), sections.get("tips"), sections.get("alerts")))
                )

                if needs_repair:
                    try:
                        repaired = repair_plan_output(full_text)
                        repaired_sessions = repaired.get("sessions", [])
                        if isinstance(repaired_sessions, list):
                            validated = StudyPlanResult(sessions=repaired_sessions)
                            timetable_sessions = [s.model_dump() for s in validated.sessions]

                        sections = {
                            "summary": str(repaired.get("summary", "") or "").strip(),
                            "tips": str(repaired.get("tips", "") or "").strip(),
                            "alerts": str(repaired.get("alerts", "") or "").strip(),
                        }
                    except Exception:
                        pass

                if user_context is not None and extraction_result is not None:
                    timetable_sessions = enforce_planning_constraints(
                        sessions=timetable_sessions,
                        user_context=user_context,
                        extraction_result=extraction_result,
                    )

                # Calculate actual scheduled study hours using session durations, not session counts
                total_scheduled_mins = 0
                for s in timetable_sessions:
                    s_start = _time_to_minutes(s.get("start", ""))
                    s_end = _time_to_minutes(s.get("end", ""))
                    if s_start is not None and s_end is not None and s_end > s_start:
                        total_scheduled_mins += (s_end - s_start)
                scheduled_hours = round(total_scheduled_mins / 60.0, 1)

                prefs = (user_context or {}).get("preferences", {}) or {}
                intensity_key = str(prefs.get("study_intensity", "balanced")).strip().lower()
                intensity_label, hrs_min, hrs_max = INTENSITY_HOURS.get(intensity_key, ("Steady Progress", 16, 25))

                limitation_note = None
                if scheduled_hours < hrs_min:
                    limitation_note = (
                        f"Target study workload was {hrs_min}–{hrs_max} hrs/week ({intensity_label}), "
                        f"but only {scheduled_hours} hrs ({total_scheduled_mins} mins across {len(timetable_sessions)} sessions) could be scheduled. "
                        f"The full weekly target could not fit due to schedule constraints "
                        f"(classes, recurring commitments, meal/rest breaks, and allowed study days). "
                        f"All available realistic study slots have been scheduled."
                    )
                    workload_header = (
                        f"**Study Intensity Target**: {intensity_label} ({hrs_min}–{hrs_max} hrs/week)\n"
                        f"**Actual Scheduled Study Time**: {scheduled_hours} hrs ({total_scheduled_mins} mins, {len(timetable_sessions)} sessions)\n"
                        f"**Schedule Limitation**: {limitation_note}\n\n"
                    )
                else:
                    workload_header = (
                        f"**Study Intensity Target**: {intensity_label} ({hrs_min}–{hrs_max} hrs/week)\n"
                        f"**Actual Scheduled Study Time**: {scheduled_hours} hrs ({total_scheduled_mins} mins, {len(timetable_sessions)} sessions)\n"
                        f"**Schedule Status**: Target successfully met.\n\n"
                    )

                if sections.get("summary"):
                    sections["summary"] = workload_header + sections["summary"]
                else:
                    sections["summary"] = workload_header.strip()

                yield {
                    "done": True,
                    "timetable_json": timetable_sessions,
                    "sections": sections,
                    "full_text": full_text,
                    "scheduled_hours": scheduled_hours,
                    "target_hrs_min": hrs_min,
                    "target_hrs_max": hrs_max,
                    "intensity_label": intensity_label,
                    "limitation_note": limitation_note,
                }
                return

            elif "internalServerException" in event:
                yield {"error": str(event["internalServerException"])}
                return
            elif "validationException" in event:
                yield {"error": str(event["validationException"])}
                return
            elif "throttlingException" in event:
                yield {"error": str(event["throttlingException"])}
                return
            elif "modelStreamErrorException" in event:
                yield {"error": str(event["modelStreamErrorException"])}
                return

    except Exception as exc:
        yield {"error": str(exc)}