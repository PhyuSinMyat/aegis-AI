"""
Database migration script to add authentication and profile features
Run this once to update your database schema
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "users.db"


def migrate_database():
    """Add new columns and tables for authentication features"""
    print("Starting database migration...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Add new profile fields to users table
        print("\n1. Adding profile fields to users table...")

        new_columns = [
            ("first_name", "TEXT NOT NULL DEFAULT ''"),
            ("last_name", "TEXT NOT NULL DEFAULT ''"),
            ("date_of_birth", "TEXT NOT NULL DEFAULT ''"),
            ("phone_number", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT DEFAULT ''"),
        ]

        for column_name, column_def in new_columns:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_def}")
                print(f"   [OK] Added column: {column_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print(f"   [SKIP] Column {column_name} already exists")
                else:
                    raise

        # 2. Create password_reset_tokens table
        print("\n2. Creating password_reset_tokens table...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                used BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("   [OK] password_reset_tokens table created")

        # 3. Create index for faster lookups
        print("\n3. Creating indexes...")
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_password_reset_email
            ON password_reset_tokens(email)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_password_reset_expires
            ON password_reset_tokens(expires_at)
        ''')
        print("   [OK] Indexes created")

        conn.commit()
        print("\n[SUCCESS] Migration completed successfully!")
        print("\nNew features available:")
        print("  - Password reset via email OTP")
        print("  - Extended user profiles (first/last name, DOB, phone)")
        print("  - User account creation timestamps")

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_database()
