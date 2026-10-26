import json
from datetime import datetime
from typing import Dict, List, Any

from llm_service import generate_response

REPLANNING_SYSTEM_PROMPT = """
You are a highly intelligent study-replanning assistant.
You will be provided with:
1. The student's current weekly study schedule (JSON array).
2. The details of a study session they just missed or failed to complete.
3. Today's date, day of week, and current time.

Your task is to intelligently handle this missed session without blindly moving it to the next day.

Step 1 — Analyze importance of the missed session:
- Is it preparation for an upcoming class (e.g. next day)?
- Is there a nearby assessment or deadline?
- Is it a core/foundational topic?
- Or is it just general revision?

Step 2 — Decide the best action based on importance:
- High importance: reschedule soon (e.g., next available day or earliest slot).
- Medium importance: reschedule but not necessarily next day, or merge with an existing session of the same module.
- Low importance: optionally skip OR push to later in the week to avoid overloading the schedule.

Step 3 — Apply minimal changes:
- Do NOT regenerate the full weekly plan.
- Only adjust what is exactly necessary to accommodate the change.
- Keep the timetable stable and realistic.
- Avoid creating overloaded days.
- Respect existing constraints (classes, commitments, study limits).
- Keep behavior practical and human-like.
- Prioritize academic urgency over perfect scheduling.

CRITICAL scheduling rules:
- You MUST only reschedule to a FUTURE day or a later time slot TODAY. Never reschedule to a day that has already passed this week.
- The week runs Monday to Sunday. Use today's date and day to determine which days are still in the future.
- When adding a new session, place it in a gap where no other session exists. Do not overlap with existing sessions.
- Keep the original missed session in the timetable as-is (do not remove it). Add the NEW rescheduled session as an additional entry.

Each timetable entry MUST use this exact format:
{
  "day": "Monday",
  "start": "HH:MM",
  "end": "HH:MM",
  "subject": "Module Name",
  "topic": "Topic description",
  "focus": "What to focus on"
}

Return ONLY valid JSON with this exact schema:
{
  "patched_timetable": [ ... complete updated schedule array with the new rescheduled session added ... ],
  "explanation": "A short, one-sentence UI message explaining the change (e.g. 'Rescheduled to Wednesday 18:00-19:00', or 'Merged with Thursday slot').",
  "is_rescheduled": true
}
If the session is entirely skipped because of low priority and no new timeslot is found, return "is_rescheduled": false.
Do not output any markdown formatting, only raw JSON.
""".strip()

def evaluate_and_replan(
    current_timetable: List[Dict[str, Any]],
    missed_session: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Evaluates a missed session and returns a patched timetable.
    """
    now = datetime.now()
    today_day = now.strftime("%A")
    today_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    today_idx = days_order.index(today_day) if today_day in days_order else 0
    remaining_days = days_order[today_idx:]

    prompt = f"""
Today is {today_day}, {today_date}. Current time: {current_time}.
Remaining days this week (today and after): {', '.join(remaining_days)}.

Current Timetable:
{json.dumps(current_timetable, indent=2)}

Missed Session:
Module: {missed_session.get('module_name')}
Original Day/Time: {missed_session.get('actual_start')} to {missed_session.get('actual_end')}
Planned Duration: {missed_session.get('planned_duration_mins')} minutes

Reschedule this missed session to a future available slot. Output the full updated timetable JSON with the new session added.
"""
    content_blocks = [{"text": prompt}]
    
    try:
        response_text = generate_response(
            content_blocks,
            system_prompt=REPLANNING_SYSTEM_PROMPT,
            max_tokens=4000
        )
        
        # Clean up possible markdown wrappers
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
            
        result = json.loads(text.strip())

        patched = result.get("patched_timetable", [])
        original_keys = set()
        for s in current_timetable:
            original_keys.add((s.get("day"), s.get("start"), s.get("end"), s.get("subject")))

        missed_day = ""
        raw_start = missed_session.get("actual_start", "")
        if "T" in raw_start:
            from datetime import datetime as _dt
            try:
                missed_day = _dt.fromisoformat(raw_start).strftime("%A")
            except Exception:
                pass

        for s in patched:
            key = (s.get("day"), s.get("start"), s.get("end"), s.get("subject"))
            if key not in original_keys:
                s["replanned"] = True
                s["replanned_from"] = missed_day or "a missed session"
            if s.get("replanned") and key in original_keys:
                original_keys.discard(key)

        print(f"[ReplanningAgent] Replanning successful. is_rescheduled={result.get('is_rescheduled')}, explanation={result.get('explanation')}")
        print(f"[ReplanningAgent] Original timetable had {len(current_timetable)} sessions, patched has {len(patched)} sessions")
        return result
    except Exception as e:
        print(f"[ReplanningAgent] Replanning failed: {e}")
        return {"is_rescheduled": False, "explanation": "Replanning failed due to an error.", "patched_timetable": current_timetable}
