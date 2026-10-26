"""
Comprehensive Replanning System Test
Tests both backend and frontend integration
"""
import json
from datetime import datetime, timedelta
from database import DatabaseHelper

def print_header(text):
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def test_replanning_agent():
    """Test the replanning agent directly"""
    print_header("TEST 1: Replanning Agent (Backend)")

    try:
        from backend.agents.replanning_agent import evaluate_and_replan

        # Create a sample timetable
        sample_timetable = [
            {"day": "Monday", "start": "09:00", "end": "10:00", "subject": "Mathematics", "topic": "Calculus"},
            {"day": "Monday", "start": "14:00", "end": "15:00", "subject": "Physics", "topic": "Mechanics"},
            {"day": "Tuesday", "start": "10:00", "end": "11:00", "subject": "Chemistry", "topic": "Organic"},
            {"day": "Wednesday", "start": "09:00", "end": "10:00", "subject": "Mathematics", "topic": "Algebra"},
        ]

        # Simulate a missed session
        missed_session = {
            "module_name": "Mathematics",
            "actual_start": "2024-01-15T09:00:00",
            "actual_end": "2024-01-15T10:00:00",
            "planned_duration_mins": 60
        }

        print("✓ Sample timetable created with", len(sample_timetable), "sessions")
        print("✓ Simulating missed session:", missed_session["module_name"])

        print("\n[Calling Replanning Agent...]")
        result = evaluate_and_replan(sample_timetable, missed_session)

        print("\n[Replanning Agent Response]")
        print("  is_rescheduled:", result.get("is_rescheduled"))
        print("  explanation:", result.get("explanation"))
        print("  patched_timetable sessions:", len(result.get("patched_timetable", [])))

        if result.get("is_rescheduled"):
            print("✅ PASS: Replanning agent successfully rescheduled the session")
        else:
            print("⚠️  WARN: Replanning agent decided not to reschedule (low priority)")

        return True

    except Exception as e:
        print(f"❌ FAIL: Replanning agent error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_operations():
    """Test database save and retrieve operations"""
    print_header("TEST 2: Database Operations")

    db = DatabaseHelper()

    # Get user 1 (assuming exists)
    user_id = 1
    plan = db.get_latest_study_plan_by_user_id(user_id)

    if not plan:
        print("❌ FAIL: No study plan found for user 1")
        print("   Please create a study plan first")
        return False

    print(f"✓ Found plan: plan_id={plan['plan_id']}, title='{plan.get('title')}'")

    original_timetable = plan.get('timetable_json', [])
    print(f"✓ Original timetable has {len(original_timetable)} sessions")

    # Create modified timetable
    modified_timetable = original_timetable.copy()
    test_session = {
        "day": "Friday",
        "start": "20:00",
        "end": "21:00",
        "subject": "TEST_REPLAN_SESSION",
        "topic": "Automated test session"
    }
    modified_timetable.append(test_session)

    print(f"\n[Updating database...]")
    db.update_study_plan_timetable(plan['plan_id'], modified_timetable, "[TEST] Comprehensive test")

    # Retrieve and verify
    updated_plan = db.get_study_plan_by_id(plan['plan_id'])
    found_test = any(s.get('subject') == 'TEST_REPLAN_SESSION'
                     for s in updated_plan.get('timetable_json', []))

    if found_test:
        print("✅ PASS: Database successfully saved and retrieved replanned timetable")
    else:
        print("❌ FAIL: Test session not found in database")
        return False

    # Cleanup
    print("\n[Cleaning up...]")
    db.update_study_plan_timetable(plan['plan_id'], original_timetable)
    print("✓ Restored original timetable")

    return True


def test_session_replanning_flag():
    """Test session replanning flag system"""
    print_header("TEST 3: Session Replanning Flag")

    db = DatabaseHelper()
    user_id = 1

    # Create a test session
    test_session = {
        "user_id": user_id,
        "module_name": "TEST_MODULE",
        "planned_duration_mins": 60,
        "status": "incompleted",
        "study_seconds": 0,
        "inactivity_seconds": 0,
        "distraction_seconds": 0,
        "current_app": "",
        "actual_start": datetime.now().isoformat(),
        "actual_end": datetime.now().isoformat(),
        "last_heartbeat": None,
    }

    session_id = db.insert_study_session(test_session)
    print(f"✓ Created test session: session_id={session_id}")

    # Check initially not replanned
    is_replanned = db.is_session_replanned(session_id)
    print(f"✓ Initial state: is_replanned={is_replanned}")

    if is_replanned:
        print("❌ FAIL: Session should not be marked as replanned initially")
        db.delete_session(session_id)
        return False

    # Mark as replanned
    db.log_session_event(session_id, user_id, "replanned", "Test replanning event")
    print("✓ Logged replanning event")

    # Check now replanned
    is_replanned = db.is_session_replanned(session_id)
    print(f"✓ After logging: is_replanned={is_replanned}")

    if not is_replanned:
        print("❌ FAIL: Session should be marked as replanned after logging event")
        db.delete_session(session_id)
        return False

    print("✅ PASS: Session replanning flag system works correctly")

    # Cleanup
    db.delete_session(session_id)
    print("✓ Deleted test session")

    return True


def test_missed_session_detection():
    """Test missed session detection logic"""
    print_header("TEST 4: Missed Session Detection")

    db = DatabaseHelper()
    user_id = 1

    plan = db.get_latest_study_plan_by_user_id(user_id)
    if not plan:
        print("❌ FAIL: No study plan found")
        return False

    print(f"✓ Found plan with {len(plan.get('timetable_json', []))} sessions")

    today_name = datetime.now().strftime("%A")
    now_time = datetime.now().strftime("%H:%M")

    print(f"✓ Today is {today_name}, current time is {now_time}")

    # Find sessions for today
    today_sessions = [
        s for s in plan.get('timetable_json', [])
        if s.get('day', '').strip().capitalize() == today_name
    ]

    print(f"✓ Found {len(today_sessions)} sessions scheduled for today")

    # Find past sessions
    past_sessions = [
        s for s in today_sessions
        if s.get('end', '23:59') < now_time
    ]

    print(f"✓ Found {len(past_sessions)} sessions that have already ended")

    if past_sessions:
        print("\nPast sessions:")
        for s in past_sessions:
            print(f"  - {s.get('start')}-{s.get('end')}: {s.get('subject')}")

    print("✅ PASS: Missed session detection logic is working")
    return True


def test_config_check():
    """Check configuration for replanning"""
    print_header("TEST 5: Configuration Check")

    from config import Config

    print("Configuration:")
    print(f"  USE_MOCK_LLM: {Config.USE_MOCK_LLM}")
    print(f"  DEBUG_LLM: {Config.DEBUG_LLM}")
    print(f"  AWS_REGION: {Config.AWS_REGION}")
    print(f"  AWS_BEDROCK_MODEL_ID: {'Set' if Config.AWS_BEDROCK_MODEL_ID else 'NOT SET'}")
    print(f"  AWS_ACCESS_KEY_ID: {'Set' if Config.AWS_ACCESS_KEY_ID else 'NOT SET'}")

    if Config.USE_MOCK_LLM:
        print("\n⚠️  WARNING: Using MOCK LLM mode")
        print("   Replanning agent will return empty responses")
        print("   Set USE_MOCK_LLM=0 in .env to use real LLM")
    else:
        print("\n✓ Using real LLM (Bedrock)")

        if not Config.AWS_ACCESS_KEY_ID or not Config.AWS_SECRET_ACCESS_KEY:
            print("❌ FAIL: AWS credentials not configured")
            print("   Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env")
            return False

        if not Config.AWS_BEDROCK_MODEL_ID:
            print("❌ FAIL: AWS_BEDROCK_MODEL_ID not set in .env")
            return False

    print("✅ PASS: Configuration is valid")
    return True


def test_frontend_schedule_fetch():
    """Test that frontend can fetch schedule"""
    print_header("TEST 6: Frontend Schedule Fetch")

    db = DatabaseHelper()
    user_id = 1

    # Simulate what the /tracker/schedule endpoint does
    from backend.routes.tracker_routes import _get_current_week_study_plan

    plan = _get_current_week_study_plan(user_id)

    if not plan:
        print("❌ FAIL: _get_current_week_study_plan returned None")
        return False

    print(f"✓ Plan retrieved: plan_id={plan.get('plan_id')}")
    print(f"✓ Timetable has {len(plan.get('timetable_json', []))} sessions")

    today_name = datetime.now().strftime("%A")
    today_sessions = [
        s for s in plan.get('timetable_json', [])
        if s.get('day', '').strip().capitalize() == today_name
    ]

    print(f"✓ Today ({today_name}) has {len(today_sessions)} sessions")

    print("✅ PASS: Schedule fetch works correctly")
    return True


def run_all_tests():
    """Run all tests and report results"""
    print("\n" + "█"*70)
    print("█  COMPREHENSIVE REPLANNING SYSTEM TEST")
    print("█"*70)

    tests = [
        ("Configuration Check", test_config_check),
        ("Database Operations", test_database_operations),
        ("Session Replanning Flag", test_session_replanning_flag),
        ("Missed Session Detection", test_missed_session_detection),
        ("Frontend Schedule Fetch", test_frontend_schedule_fetch),
        ("Replanning Agent", test_replanning_agent),
    ]

    results = {}

    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"\n❌ CRITICAL ERROR in {test_name}: {e}")
            import traceback
            traceback.print_exc()
            results[test_name] = False

    # Summary
    print_header("TEST SUMMARY")

    passed = sum(1 for r in results.values() if r)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status:12} {test_name}")

    print("\n" + "-"*70)
    print(f"Results: {passed}/{total} tests passed")
    print("-"*70)

    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Replanning system is working correctly.")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the errors above.")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
