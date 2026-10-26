#!/usr/bin/env python
"""
test_scheduler.py
-----------------
Manual test script to run the daily summary job outside the scheduler.
This helps separate scheduler issues from summary-generation issues.

Usage:
    python test_scheduler.py [--user USER_ID]

Without --user: runs for all active users
With --user 1: runs only for user 1 (useful for quick iteration)
"""

import logging
import sys
from argparse import ArgumentParser

from app import create_app
from database import DatabaseHelper
from backend.services.summary_scheduler import (
    run_daily_summary_job,
    generate_summaries_for_user,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
logger = logging.getLogger("test_scheduler")

print("="*80)
print("MANUAL SCHEDULER TEST")
print("="*80)

# Parse arguments
parser = ArgumentParser(description="Manually run the daily summary job for testing")
parser.add_argument("--user", type=int, default=None, help="Run for specific user only (1-based)")
args = parser.parse_args()

# Initialize app and DB
logger.info("Creating Flask app...")
app = create_app()
db = DatabaseHelper()

try:
    if args.user:
        logger.info(f"Running summary generation for user_id={args.user} only")
        with app.app_context():
            count = generate_summaries_for_user(db, args.user)
            logger.info(f"Result: {count} card(s) saved for user {args.user}")
    else:
        logger.info("Running full daily summary job for ALL active users")
        run_daily_summary_job(app, db)
    
    logger.info("✓ Test completed successfully!")
except Exception as exc:
    logger.error(f"✗ Test failed with error: {exc}", exc_info=True)
    sys.exit(1)

print("="*80)
