"""
Test script to verify replanning database operations.
Run this to ensure the replanned timetable is correctly saved and retrieved.
"""
import json
from database import DatabaseHelper

def test_replanning():
    db = DatabaseHelper()

    # Get a user (assuming user_id=1 exists)
    user_id = 1

    # Get the latest study plan
    plan = db.get_latest_study_plan_by_user_id(user_id)
    if not plan:
        print("❌ No study plan found for user 1")
        return

    print(f"✓ Found study plan: plan_id={plan['plan_id']}, title={plan.get('title')}")
    print(f"✓ Original timetable has {len(plan.get('timetable_json', []))} sessions")

    # Display the original timetable
    print("\nOriginal timetable:")
    for i, session in enumerate(plan.get('timetable_json', [])[:5]):  # Show first 5
        print(f"  {i+1}. {session.get('day')} {session.get('start')}-{session.get('end')}: {session.get('subject')}")

    # Create a modified timetable (add a test session)
    modified_timetable = plan['timetable_json'].copy()
    modified_timetable.append({
        "day": "Monday",
        "start": "20:00",
        "end": "21:00",
        "subject": "TEST REPLANNED SESSION",
        "topic": "Testing replanning functionality"
    })

    print(f"\n✓ Modified timetable has {len(modified_timetable)} sessions (added 1 test session)")

    # Update the plan
    print(f"\nUpdating plan {plan['plan_id']} with replanned timetable...")
    db.update_study_plan_timetable(plan['plan_id'], modified_timetable, "[TEST] Replanning test")

    # Retrieve the plan again
    updated_plan = db.get_study_plan_by_id(plan['plan_id'])
    print(f"\n✓ Retrieved updated plan: {len(updated_plan.get('timetable_json', []))} sessions")

    # Check if the test session is present
    test_session = next((s for s in updated_plan.get('timetable_json', [])
                        if s.get('subject') == 'TEST REPLANNED SESSION'), None)

    if test_session:
        print("✅ SUCCESS: Replanned session found in database!")
        print(f"   {test_session.get('day')} {test_session.get('start')}-{test_session.get('end')}: {test_session.get('subject')}")
    else:
        print("❌ FAIL: Replanned session NOT found in database!")

    # Cleanup: restore original timetable
    print("\nRestoring original timetable...")
    db.update_study_plan_timetable(plan['plan_id'], plan['timetable_json'])
    restored_plan = db.get_study_plan_by_id(plan['plan_id'])
    print(f"✓ Restored: {len(restored_plan.get('timetable_json', []))} sessions")

    print("\n" + "="*60)
    print("Test completed successfully!")
    print("="*60)

if __name__ == "__main__":
    test_replanning()
