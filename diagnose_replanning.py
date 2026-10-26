#!/usr/bin/env python3
"""
Quick Diagnostic Tool for Replanning System
Checks all components and reports status
"""
import sys
from pathlib import Path

def colored(text, color):
    """Add color to terminal output"""
    colors = {
        'red': '\033[91m',
        'green': '\033[92m',
        'yellow': '\033[93m',
        'blue': '\033[94m',
        'reset': '\033[0m'
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"

def check_mark(status):
    """Return check mark or X based on status"""
    return colored("✅", "green") if status else colored("❌", "red")

def print_section(title):
    """Print section header"""
    print(f"\n{colored('═' * 60, 'blue')}")
    print(colored(f" {title}", 'blue'))
    print(colored('═' * 60, 'blue'))

def check_imports():
    """Check if all required modules can be imported"""
    print_section("1. Python Imports")

    checks = {
        "Database": ("from database import DatabaseHelper", "database.py missing or has syntax errors"),
        "Config": ("from config import Config", "config.py missing or has syntax errors"),
        "Replanning Agent": ("from backend.agents.replanning_agent import evaluate_and_replan", "replanning_agent.py missing"),
        "LLM Service": ("from llm_service import generate_response", "llm_service.py missing"),
        "Tracker Routes": ("from backend.routes.tracker_routes import _get_current_week_study_plan", "tracker_routes.py missing"),
    }

    results = {}
    for name, (import_cmd, error_msg) in checks.items():
        try:
            exec(import_cmd)
            results[name] = True
            print(f"{check_mark(True)} {name}")
        except Exception as e:
            results[name] = False
            print(f"{check_mark(False)} {name}: {error_msg}")
            print(f"   Error: {e}")

    return all(results.values()), results

def check_config():
    """Check configuration settings"""
    print_section("2. Configuration")

    try:
        from config import Config

        checks = {
            "AWS Region": Config.AWS_REGION,
            "Bedrock Model ID": Config.AWS_BEDROCK_MODEL_ID,
            "Bedrock API Key": Config.AWS_BEARER_TOKEN_BEDROCK,
            "AWS Access Key": Config.AWS_ACCESS_KEY_ID,
            "AWS Secret Key": Config.AWS_SECRET_ACCESS_KEY,
        }

        print(f"USE_MOCK_LLM: {colored(str(Config.USE_MOCK_LLM), 'yellow' if Config.USE_MOCK_LLM else 'green')}")
        print(f"DEBUG_LLM: {Config.DEBUG_LLM}")
        print(f"AWS_REGION: {Config.AWS_REGION or colored('NOT SET', 'red')}")
        print(f"AWS_BEDROCK_MODEL_ID: {colored('SET', 'green') if Config.AWS_BEDROCK_MODEL_ID else colored('NOT SET', 'red')}")
        print(f"AWS_BEARER_TOKEN_BEDROCK: {colored('SET', 'green') if Config.AWS_BEARER_TOKEN_BEDROCK else colored('NOT SET', 'red')}")
        print(f"AWS_ACCESS_KEY_ID: {colored('SET', 'green') if Config.AWS_ACCESS_KEY_ID else colored('NOT SET', 'red')}")
        print(f"AWS_SECRET_ACCESS_KEY: {colored('SET', 'green') if Config.AWS_SECRET_ACCESS_KEY else colored('NOT SET', 'red')}")

        if Config.USE_MOCK_LLM:
            print(f"\n{colored('⚠️  WARNING:', 'yellow')} Mock LLM mode is enabled!")
            print("   Replanning will not work properly.")
            print("   Set USE_MOCK_LLM=0 in .env file")
            return False

        has_api_key_auth = bool(Config.AWS_BEARER_TOKEN_BEDROCK)
        has_access_key_auth = bool(Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY)

        if not Config.AWS_BEDROCK_MODEL_ID or not (has_api_key_auth or has_access_key_auth):
            print(f"\n{colored('❌ FAIL:', 'red')} AWS credentials not fully configured")
            print("   Check your .env file")
            return False

        print(f"\n{colored('✅ PASS:', 'green')} Configuration is valid")
        return True

    except Exception as e:
        print(f"{colored('❌ ERROR:', 'red')} Could not load config: {e}")
        return False

def check_database():
    """Check database connection and data"""
    print_section("3. Database")

    try:
        from database import DatabaseHelper
        db = DatabaseHelper()

        print(f"{check_mark(True)} Database connection established")
        print(f"   Database path: {db.db_path}")

        # Check if database file exists
        if not db.db_path.exists():
            print(f"{check_mark(False)} Database file does not exist!")
            return False

        # Check for users
        try:
            users = db._get_connection().execute("SELECT COUNT(*) FROM users").fetchone()[0]
            print(f"{check_mark(users > 0)} Users in database: {users}")
        except:
            print(f"{check_mark(False)} Could not query users table")
            return False

        # Check for study plans
        try:
            plans = db._get_connection().execute("SELECT COUNT(*) FROM study_plans").fetchone()[0]
            print(f"{check_mark(plans > 0)} Study plans in database: {plans}")

            if plans == 0:
                print(f"   {colored('⚠️  WARNING:', 'yellow')} No study plans found")
                print("   Generate a study plan from the web app first")
                return False
        except:
            print(f"{check_mark(False)} Could not query study_plans table")
            return False

        # Check specific user
        user_id = 1
        plan = db.get_latest_study_plan_by_user_id(user_id)
        if plan:
            print(f"{check_mark(True)} User {user_id} has study plan: plan_id={plan['plan_id']}")
            print(f"   Title: {plan.get('title', 'N/A')}")
            print(f"   Sessions: {len(plan.get('timetable_json', []))}")
        else:
            print(f"{check_mark(False)} User {user_id} has no study plan")
            print("   Log in and generate a plan first")
            return False

        print(f"\n{colored('✅ PASS:', 'green')} Database is properly set up")
        return True

    except Exception as e:
        print(f"{colored('❌ ERROR:', 'red')} Database check failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_replanning_files():
    """Check that all replanning files exist"""
    print_section("4. File Structure")

    base_dir = Path(__file__).parent
    files = {
        "backend/agents/replanning_agent.py": "Replanning agent implementation",
        "backend/routes/tracker_routes.py": "Tracker routes with replanning endpoints",
        "frontend/templates/tracker.html": "Tracker page template",
        "database.py": "Database helper with replanning methods",
        "llm_service.py": "LLM service for agent",
        "config.py": "Configuration loader",
    }

    all_exist = True
    for file_path, description in files.items():
        full_path = base_dir / file_path
        exists = full_path.exists()
        print(f"{check_mark(exists)} {file_path}")
        if not exists:
            print(f"   {description}")
            all_exist = False

    if all_exist:
        print(f"\n{colored('✅ PASS:', 'green')} All required files exist")
    else:
        print(f"\n{colored('❌ FAIL:', 'red')} Some files are missing")

    return all_exist

def check_frontend_integration():
    """Check frontend integration points"""
    print_section("5. Frontend Integration")

    try:
        base_dir = Path(__file__).parent
        tracker_html = base_dir / "frontend" / "templates" / "tracker.html"

        if not tracker_html.exists():
            print(f"{check_mark(False)} tracker.html not found")
            return False

        content = tracker_html.read_text(encoding='utf-8')

        checks = {
            "loadSchedule function": "async function loadSchedule()" in content,
            "replanSession function": "async function replanSession(" in content,
            "Schedule refresh after auto-replan": "loadSchedule();" in content and "replan_notification" in content,
            "Schedule refresh after manual replan": "setTimeout(() => { loadSchedule(); }, 500);" in content or "setTimeout(() => {\n                loadSchedule();" in content,
            "Replan button in HTML": 'onclick="replanSession(' in content,
        }

        for check_name, result in checks.items():
            print(f"{check_mark(result)} {check_name}")

        if all(checks.values()):
            print(f"\n{colored('✅ PASS:', 'green')} Frontend integration is correct")
            return True
        else:
            print(f"\n{colored('❌ FAIL:', 'red')} Some frontend checks failed")
            return False

    except Exception as e:
        print(f"{colored('❌ ERROR:', 'red')} Could not check frontend: {e}")
        return False

def check_backend_routes():
    """Check backend routes are properly configured"""
    print_section("6. Backend Routes")

    try:
        from backend.routes import tracker_routes

        checks = {
            "_auto_replan_missed_session": hasattr(tracker_routes, "_auto_replan_missed_session"),
            "_record_missed_slots": hasattr(tracker_routes, "_record_missed_slots"),
            "_get_current_week_study_plan": hasattr(tracker_routes, "_get_current_week_study_plan"),
            "get_schedule": hasattr(tracker_routes, "get_schedule"),
            "replan_session": hasattr(tracker_routes, "replan_session"),
        }

        for check_name, result in checks.items():
            print(f"{check_mark(result)} Function: {check_name}")

        if all(checks.values()):
            print(f"\n{colored('✅ PASS:', 'green')} Backend routes are complete")
            return True
        else:
            print(f"\n{colored('❌ FAIL:', 'red')} Some backend functions are missing")
            return False

    except Exception as e:
        print(f"{colored('❌ ERROR:', 'red')} Could not check backend routes: {e}")
        return False

def run_mini_test():
    """Run a mini functional test"""
    print_section("7. Functional Test")

    try:
        from database import DatabaseHelper
        from backend.agents.replanning_agent import evaluate_and_replan

        db = DatabaseHelper()
        plan = db.get_latest_study_plan_by_user_id(1)

        if not plan:
            print(f"{colored('⚠️  SKIP:', 'yellow')} No study plan to test with")
            return True

        print("Testing replanning agent with sample data...")

        sample_timetable = [
            {"day": "Monday", "start": "09:00", "end": "10:00", "subject": "Test", "topic": "Test"}
        ]

        missed_session = {
            "module_name": "Test Module",
            "actual_start": "2024-01-15T09:00:00",
            "actual_end": "2024-01-15T10:00:00",
            "planned_duration_mins": 60
        }

        result = evaluate_and_replan(sample_timetable, missed_session)

        has_keys = all(k in result for k in ["is_rescheduled", "explanation", "patched_timetable"])

        print(f"{check_mark(has_keys)} Replanning agent returned valid response")
        print(f"   is_rescheduled: {result.get('is_rescheduled')}")
        print(f"   explanation: {result.get('explanation', 'N/A')[:60]}...")

        if has_keys:
            print(f"\n{colored('✅ PASS:', 'green')} Functional test successful")
            return True
        else:
            print(f"\n{colored('❌ FAIL:', 'green')} Invalid response from agent")
            return False

    except Exception as e:
        print(f"{colored('❌ ERROR:', 'red')} Functional test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all diagnostic checks"""
    print(colored("\n╔══════════════════════════════════════════════════════════╗", "blue"))
    print(colored("║  REPLANNING SYSTEM DIAGNOSTIC TOOL                       ║", "blue"))
    print(colored("╚══════════════════════════════════════════════════════════╝", "blue"))

    results = {}

    results["imports"], _ = check_imports()
    results["config"] = check_config()
    results["database"] = check_database()
    results["files"] = check_replanning_files()
    results["frontend"] = check_frontend_integration()
    results["backend"] = check_backend_routes()
    results["functional"] = run_mini_test()

    # Summary
    print_section("DIAGNOSTIC SUMMARY")

    for check_name, passed in results.items():
        status = colored("PASS", "green") if passed else colored("FAIL", "red")
        print(f"  {status:20} {check_name.replace('_', ' ').title()}")

    passed_count = sum(results.values())
    total_count = len(results)

    print(f"\n{colored('─' * 60, 'blue')}")
    print(f"  Result: {passed_count}/{total_count} checks passed")
    print(colored('─' * 60, 'blue'))

    if passed_count == total_count:
        print(f"\n{colored('🎉 SUCCESS:', 'green')} Replanning system is properly configured!")
        print("\nNext steps:")
        print("  1. Start the Flask server: python app.py")
        print("  2. Open http://localhost:5000/tracker")
        print("  3. Miss a session and check for replanning")
        return 0
    else:
        print(f"\n{colored('⚠️  ISSUES FOUND:', 'yellow')} Please fix the failed checks above")
        print("\nRecommended actions:")
        if not results["config"]:
            print("  - Check your .env file and set AWS credentials")
        if not results["database"]:
            print("  - Generate a study plan from the web app")
        if not results["imports"]:
            print("  - Run: pip install -r requirements.txt")
        if not results["frontend"] or not results["backend"]:
            print("  - Verify recent code changes were applied correctly")
        print("\nFor detailed testing, run: python test_replanning_comprehensive.py")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{colored('Diagnostic cancelled by user', 'yellow')}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{colored('FATAL ERROR:', 'red')} {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
