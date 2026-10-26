#!/usr/bin/env python
"""
test_planning_scheduler.py
--------------------------
Manual test script to run the weekly planning job outside the scheduler.
This helps test plan generation and debug issues independently of the scheduler.

Usage:
    python test_planning_scheduler.py [--user USER_ID]

Without --user: runs for all active users
With --user 1: runs only for user 1 (useful for quick iteration)
"""

import logging
import sys
from argparse import ArgumentParser

from app import create_app
from database import DatabaseHelper
from backend.services.planning_scheduler import (
    run_weekly_planning_job,
    generate_plan_for_user,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
logger = logging.getLogger("test_planning_scheduler")

print("="*80)
print("MANUAL PLANNING SCHEDULER TEST")
print("="*80)

# Parse arguments
parser = ArgumentParser(description="Manually run the weekly planning job for testing")
parser.add_argument("--user", type=int, default=None, help="Run for specific user only (1-based)")
args = parser.parse_args()

# Initialize app and DB
logger.info("Creating Flask app...")
app = create_app()
db = DatabaseHelper()

try:
    if args.user:
        logger.info(f"Running plan generation for user_id={args.user} only")
        with app.app_context():
            result = generate_plan_for_user(db, args.user)

            if result["success"]:
                logger.info(
                    f"✓ Success: Week1 plan_id={result.get('week1_plan_id')}, "
                    f"Week2 plan_id={result.get('week2_plan_id')}"
                )
            else:
                logger.error(f"✗ Failed: {result.get('error', 'Unknown error')}")
                sys.exit(1)
    else:
        logger.info("Running full weekly planning job for ALL active users")
        run_weekly_planning_job(app, db)

    logger.info("✓ Test completed successfully!")
except Exception as exc:
    logger.error(f"✗ Test failed with error: {exc}", exc_info=True)
    sys.exit(1)

print("="*80)
