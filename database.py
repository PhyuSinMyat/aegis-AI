import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from user import User


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "users.db"


def _safe_json_loads(value: str, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


class DatabaseHelper:

    def __init__(self, db_name: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_name).resolve()
        self.init_database()

    # ──────────────────────────────────────────────
    # Connection
    # ──────────────────────────────────────────────

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────────
    # Schema
    # ──────────────────────────────────────────────

    def init_database(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT UNIQUE NOT NULL,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    profile_pic   TEXT NOT NULL DEFAULT ''
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS study_preferences (
                    user_id              INTEGER PRIMARY KEY,
                    semester_start_date  TEXT NOT NULL,
                    semester_end_date    TEXT NOT NULL DEFAULT '',
                    preferred_study_time TEXT NOT NULL,
                    study_intensity      TEXT NOT NULL DEFAULT 'balanced',
                    session_length       TEXT NOT NULL DEFAULT '60',
                    break_preference     TEXT NOT NULL DEFAULT 'medium',
                    study_days           TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri',
                    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_modules (
                    user_module_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL,
                    module_code    TEXT NOT NULL,
                    module_name    TEXT NOT NULL,
                    UNIQUE(user_id, module_code),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS study_apps (
                    study_app_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    name         TEXT NOT NULL,
                    type         TEXT NOT NULL,
                    identifier   TEXT NOT NULL,
                    purpose      TEXT,
                    UNIQUE(user_id, identifier),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS occupied_times (
                    occupied_time_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          INTEGER NOT NULL,
                    title            TEXT NOT NULL,
                    category         TEXT NOT NULL,
                    day_of_week      TEXT NOT NULL,
                    start_time       TEXT NOT NULL,
                    end_time         TEXT NOT NULL,
                    notes            TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    uploaded_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          INTEGER NOT NULL,
                    file_role        TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    stored_filename  TEXT NOT NULL,
                    file_path        TEXT NOT NULL,
                    file_extension   TEXT NOT NULL,
                    uploaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS extraction_cache (
                    user_id     INTEGER PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    cached_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS study_plans (
                    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL,
                    title          TEXT NOT NULL,
                    plan_text      TEXT NOT NULL,
                    timetable_json TEXT NOT NULL DEFAULT '[]',
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_summary_cards (
                    summary_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id            INTEGER NOT NULL,
                    summary_date       TEXT NOT NULL,
                    weekday            TEXT NOT NULL,
                    subject            TEXT NOT NULL,
                    topic              TEXT NOT NULL,
                    start_time         TEXT NOT NULL DEFAULT '',
                    end_time           TEXT NOT NULL DEFAULT '',
                    lesson_time        TEXT NOT NULL,
                    difficulty         TEXT NOT NULL DEFAULT 'Medium',
                    read_time          INTEGER NOT NULL DEFAULT 3,
                    prompt_count       INTEGER NOT NULL DEFAULT 0,
                    short_description  TEXT NOT NULL DEFAULT '',
                    summary_json       TEXT NOT NULL,
                    resources_json     TEXT NOT NULL DEFAULT '[]',
                    flashcards_json    TEXT NOT NULL DEFAULT '[]',
                    quiz_bank_json     TEXT NOT NULL DEFAULT '{}',
                    note_text          TEXT NOT NULL DEFAULT '',
                    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, summary_date, subject, topic, lesson_time),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS flashcard_progress (
                    progress_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id             INTEGER NOT NULL,
                    summary_id          INTEGER NOT NULL,
                    known_card_indices  TEXT NOT NULL DEFAULT '[]',
                    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, summary_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (summary_id) REFERENCES daily_summary_cards(summary_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS quiz_results (
                    result_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id            INTEGER NOT NULL,
                    summary_id         INTEGER NOT NULL,
                    difficulty         TEXT NOT NULL,
                    question_count     INTEGER NOT NULL,
                    correct_count      INTEGER NOT NULL,
                    answers_json       TEXT NOT NULL DEFAULT '{}',
                    results_json       TEXT NOT NULL DEFAULT '{}',
                    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (summary_id) REFERENCES daily_summary_cards(summary_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS quiz_banks (
                    quiz_bank_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id            INTEGER NOT NULL,
                    summary_id         INTEGER UNIQUE NOT NULL,
                    quiz_json          TEXT NOT NULL,
                    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (summary_id) REFERENCES daily_summary_cards(summary_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS coach_recalls (
                    user_id            INTEGER NOT NULL,
                    summary_id         INTEGER NOT NULL,
                    recalled_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, summary_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (summary_id) REFERENCES daily_summary_cards(summary_id) ON DELETE CASCADE
                )
            ''')

            # ── Migrate existing databases (safe, idempotent) ──────────────────

            # users: add profile_pic for older databases that predate this column
            user_cols = {
                row[1]
                for row in cursor.execute(
                    "PRAGMA table_info(users)"
                ).fetchall()
            }
            if 'profile_pic' not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT NOT NULL DEFAULT ''")

            # study_preferences: remove old max_study_hours column if present
            existing_cols = {
                row[1]
                for row in cursor.execute(
                    "PRAGMA table_info(study_preferences)"
                ).fetchall()
            }
            if 'max_study_hours' in existing_cols:
                rows = cursor.execute('SELECT * FROM study_preferences').fetchall()
                cursor.execute('DROP TABLE study_preferences')
                cursor.execute('''
                    CREATE TABLE study_preferences (
                        user_id              INTEGER PRIMARY KEY,
                        semester_start_date  TEXT NOT NULL,
                        semester_end_date    TEXT NOT NULL DEFAULT '',
                        preferred_study_time TEXT NOT NULL,
                        study_intensity      TEXT NOT NULL DEFAULT 'balanced',
                        session_length       TEXT NOT NULL DEFAULT '60',
                        break_preference     TEXT NOT NULL DEFAULT 'medium',
                        study_days           TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri',
                        updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')
                for row in rows:
                    cursor.execute(
                        '''
                        INSERT INTO study_preferences (
                            user_id, semester_start_date, semester_end_date,
                            preferred_study_time, study_intensity,
                            session_length, break_preference, study_days,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            row['user_id'],
                            row['semester_start_date'],
                            '',
                            row['preferred_study_time'],
                            'balanced',
                            '60',
                            'medium',
                            'Mon,Tue,Wed,Thu,Fri',
                            row['updated_at'],
                        ),
                    )


            # study_preferences: add any missing columns
            _pref_new_cols = [
                ("semester_end_date", "TEXT NOT NULL DEFAULT ''"),
                ("study_intensity",   "TEXT NOT NULL DEFAULT 'balanced'"),
                ("session_length",    "TEXT NOT NULL DEFAULT '60'"),
                ("break_preference",  "TEXT NOT NULL DEFAULT 'medium'"),
                ("study_days",        "TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri'"),
            ]
            for col_name, col_def in _pref_new_cols:
                try:
                    cursor.execute(
                        f'ALTER TABLE study_preferences ADD COLUMN {col_name} {col_def}'
                    )
                except sqlite3.OperationalError:
                    pass  # column already exists

            # daily_summary_cards: add any missing columns
            _summary_new_cols = [
                ("flashcards_json",  "TEXT NOT NULL DEFAULT '[]'"),
                ("quiz_bank_json",   "TEXT NOT NULL DEFAULT '{}'"),
                ("academic_week",    "INTEGER NOT NULL DEFAULT 0"),
            ]
            for col_name, col_def in _summary_new_cols:
                try:
                    cursor.execute(
                        f'ALTER TABLE daily_summary_cards ADD COLUMN {col_name} {col_def}'
                    )
                except sqlite3.OperationalError:
                    pass  # column already exists

            # ── Study Tracker ──────────────────────────────────────────────
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS study_sessions (
                    session_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id               INTEGER NOT NULL,
                    module_name           TEXT    NOT NULL DEFAULT '',
                    planned_duration_mins INTEGER NOT NULL DEFAULT 60,
                    actual_start          TEXT,
                    actual_end            TEXT,
                    status                TEXT    NOT NULL DEFAULT 'not_started',
                    study_seconds         INTEGER NOT NULL DEFAULT 0,
                    inactivity_seconds    INTEGER NOT NULL DEFAULT 0,
                    distraction_seconds   INTEGER NOT NULL DEFAULT 0,
                    current_app           TEXT    NOT NULL DEFAULT '',
                    last_heartbeat        TEXT,
                    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS session_events (
                    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  INTEGER NOT NULL,
                    user_id     INTEGER NOT NULL,
                    event_type  TEXT    NOT NULL,
                    details     TEXT,
                    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES study_sessions(session_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monitor_tokens (
                    token      TEXT    PRIMARY KEY,
                    user_id    INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT    NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS assignment_checker_history (
                    history_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id              INTEGER NOT NULL,
                    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    title                TEXT    NOT NULL DEFAULT 'Untitled',
                    strictness           TEXT    NOT NULL DEFAULT 'normal',
                    rubric_mode          INTEGER NOT NULL DEFAULT 0,
                    overall_score        INTEGER,
                    grade                TEXT,
                    total_marks_awarded  REAL,
                    total_marks_possible REAL,
                    submission_snippet   TEXT    NOT NULL DEFAULT '',
                    brief_snippet        TEXT    NOT NULL DEFAULT '',
                    result_json          TEXT    NOT NULL DEFAULT '{}',
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            # Migrate study_sessions: add new columns if missing
            _session_cols = [
                ("distraction_seconds", "INTEGER NOT NULL DEFAULT 0"),
                ("current_app",         "TEXT    NOT NULL DEFAULT ''"),
            ]
            for col_name, col_def in _session_cols:
                try:
                    cursor.execute(
                        f'ALTER TABLE study_sessions ADD COLUMN {col_name} {col_def}'
                    )
                except sqlite3.OperationalError:
                    pass  # column already exists

    # ──────────────────────────────────────────────
    # Users
    # ──────────────────────────────────────────────

    def row_to_user(self, row) -> Optional[User]:
        if row is None:
            return None

        def safe_get(key: str, default: str = '') -> str:
            try:
                return row[key] or default
            except (KeyError, IndexError, TypeError):
                return default

        return User(
            row['user_id'],
            row['username'],
            row['email'],
            row['password_hash'],
            safe_get('profile_pic'),
            safe_get('first_name'),
            safe_get('last_name'),
            safe_get('date_of_birth'),
            safe_get('phone_number'),
            safe_get('created_at'),
        )

    def insert_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        profile_pic: str = '',
        first_name: str = '',
        last_name: str = '',
        date_of_birth: str = '',
        phone_number: str = '',
    ) -> int:
        """Insert a new user with optional profile fields."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO users (
                    username, email, password_hash, profile_pic,
                    first_name, last_name, date_of_birth, phone_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    username.strip().lower(),
                    email.strip().lower(),
                    password_hash,
                    profile_pic or '',
                    first_name.strip(),
                    last_name.strip(),
                    date_of_birth.strip(),
                    phone_number.strip(),
                ),
            )
            return cursor.lastrowid

    def update_user_profile(
        self,
        user_id: int,
        username: str,
        profile_pic: str,
        first_name: str = '',
        last_name: str = '',
        date_of_birth: str = '',
        phone_number: str = '',
    ) -> None:
        """Update user profile including extended fields."""
        with self._get_connection() as conn:
            conn.execute(
                '''UPDATE users
                   SET username = ?, profile_pic = ?,
                       first_name = ?, last_name = ?,
                       date_of_birth = ?, phone_number = ?
                   WHERE user_id = ?''',
                (
                    username.strip().lower(),
                    profile_pic or '',
                    first_name.strip(),
                    last_name.strip(),
                    date_of_birth.strip(),
                    phone_number.strip(),
                    user_id,
                ),
            )

    def update_user_password(self, email: str, password_hash: str) -> bool:
        """Update user password by email. Returns True if successful."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                'UPDATE users SET password_hash = ? WHERE lower(email) = ?',
                (password_hash, email.strip().lower()),
            )
            return cursor.rowcount > 0

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email address."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE lower(email) = ?',
                (email.strip().lower(),),
            ).fetchone()
        return self.row_to_user(row)

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE lower(username) = ?',
                ((username or '').strip().lower(),),
            ).fetchone()
        return self.row_to_user(row)

    def get_user_by_identifier(self, identifier: str) -> Optional[User]:
        value = (identifier or '').strip().lower()
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE lower(username) = ? OR lower(email) = ?',
                (value, value),
            ).fetchone()
        return self.row_to_user(row)

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM users WHERE user_id = ?', (user_id,)
            ).fetchone()
        return self.row_to_user(row)

    def get_all_active_user_ids(self) -> List[int]:
        """Return user_id of every user (used by the scheduler)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT user_id FROM users ORDER BY user_id'
            ).fetchall()
        return [row['user_id'] for row in rows]

    def delete_user_and_related_data(self, user_id: int) -> None:
        """Delete a user and all data rows related to that user."""
        with self._get_connection() as conn:
            conn.execute('BEGIN')
            # Remove dependent rows first for compatibility with older schemas
            # where some foreign keys may not have ON DELETE CASCADE.
            conn.execute('DELETE FROM assignment_checker_history WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM flashcard_progress WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM quiz_results WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM daily_summary_cards WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM study_plans WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM extraction_cache WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM uploaded_files WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM occupied_times WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM study_apps WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM user_modules WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM study_preferences WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM users WHERE user_id = ?', (user_id,))

    # ──────────────────────────────────────────────
    # Password Reset Tokens
    # ──────────────────────────────────────────────

    def create_password_reset_token(self, email: str, otp_code: str, expires_at: str) -> None:
        """Create or replace a password reset token for an email."""
        with self._get_connection() as conn:
            # Delete any existing tokens for this email
            conn.execute('DELETE FROM password_reset_tokens WHERE email = ?', (email.strip().lower(),))
            # Insert new token
            conn.execute(
                '''INSERT INTO password_reset_tokens (email, otp_code, expires_at, attempts, used)
                   VALUES (?, ?, ?, 0, 0)''',
                (email.strip().lower(), otp_code, expires_at),
            )

    def get_password_reset_token(self, email: str) -> Optional[Dict[str, Any]]:
        """Get password reset token data for an email."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''SELECT email, otp_code, expires_at, attempts, used
                   FROM password_reset_tokens
                   WHERE email = ? AND used = 0''',
                (email.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def delete_password_reset_token(self, email: str) -> None:
        """Delete password reset token for an email."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM password_reset_tokens WHERE email = ?', (email.strip().lower(),))

    def increment_otp_attempts(self, email: str) -> int:
        """Increment OTP attempt counter and return new count."""
        with self._get_connection() as conn:
            conn.execute(
                'UPDATE password_reset_tokens SET attempts = attempts + 1 WHERE email = ?',
                (email.strip().lower(),),
            )
            row = conn.execute(
                'SELECT attempts FROM password_reset_tokens WHERE email = ?',
                (email.strip().lower(),),
            ).fetchone()
        return row['attempts'] if row else 0

    def mark_token_as_used(self, email: str) -> None:
        """Mark a password reset token as used."""
        with self._get_connection() as conn:
            conn.execute(
                'UPDATE password_reset_tokens SET used = 1 WHERE email = ?',
                (email.strip().lower(),),
            )

    def cleanup_expired_reset_tokens(self) -> None:
        """Delete expired password reset tokens."""
        from datetime import datetime
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        with self._get_connection() as conn:
            conn.execute('DELETE FROM password_reset_tokens WHERE expires_at < ?', (current_time,))

    # ──────────────────────────────────────────────
    # Study Preferences
    # ──────────────────────────────────────────────

    def save_study_preferences(
        self,
        user_id: int,
        semester_start_date:  str,
        semester_end_date:    str,
        preferred_study_time: str,
        study_intensity:      str,
        session_length:       str,
        break_preference:     str,
        study_days:           str,
    ) -> None:
        """Upsert all wizard preference fields for a user."""
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO study_preferences (
                    user_id, semester_start_date, semester_end_date,
                    preferred_study_time, study_intensity,
                    session_length, break_preference, study_days,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    semester_start_date  = excluded.semester_start_date,
                    semester_end_date    = excluded.semester_end_date,
                    preferred_study_time = excluded.preferred_study_time,
                    study_intensity      = excluded.study_intensity,
                    session_length       = excluded.session_length,
                    break_preference     = excluded.break_preference,
                    study_days           = excluded.study_days,
                    updated_at           = CURRENT_TIMESTAMP
                ''',
                (
                    user_id,
                    semester_start_date,
                    semester_end_date,
                    preferred_study_time,
                    study_intensity,
                    session_length,
                    break_preference,
                    study_days,
                ),
            )

    def get_study_preferences_by_user_id(self, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM study_preferences WHERE user_id = ?', (user_id,)
            ).fetchone()
        return dict(row) if row else None

    # ──────────────────────────────────────────────
    # User Modules
    # ──────────────────────────────────────────────

    def replace_user_modules(self, user_id: int, modules: List[Dict]) -> None:
        """Full replace: delete all existing modules for user then re-insert."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM user_modules WHERE user_id = ?', (user_id,))
            for m in modules:
                conn.execute(
                    'INSERT INTO user_modules (user_id, module_code, module_name) VALUES (?, ?, ?)',
                    (
                        user_id,
                        m.get('module_code', '').strip().upper(),
                        m.get('module_name', '').strip(),
                    ),
                )

    def get_user_modules_by_user_id(self, user_id: int) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM user_modules WHERE user_id = ? ORDER BY module_code ASC',
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ──────────────────────────────────────────────
    # Study Apps
    # ──────────────────────────────────────────────

    def replace_study_apps(self, user_id: int, study_apps: List[Dict]) -> None:
        """Full replace: wizard always sends the complete current list."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM study_apps WHERE user_id = ?', (user_id,))
            for app in study_apps:
                conn.execute(
                    '''
                    INSERT INTO study_apps (user_id, name, type, identifier, purpose)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (
                        user_id,
                        app.get('name', ''),
                        app.get('type', ''),
                        app.get('identifier', ''),
                        app.get('purpose', ''),
                    ),
                )

    def get_study_apps_by_user_id(self, user_id: int) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM study_apps WHERE user_id = ? ORDER BY study_app_id ASC',
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ──────────────────────────────────────────────
    # Occupied Times
    # ──────────────────────────────────────────────

    def replace_occupied_times(self, user_id: int, occupied_times: List[Dict]) -> None:
        """Full replace: delete all blocks then re-insert the submitted list."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM occupied_times WHERE user_id = ?', (user_id,))
            for item in occupied_times:
                conn.execute(
                    '''
                    INSERT INTO occupied_times
                        (user_id, title, category, day_of_week, start_time, end_time, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        user_id,
                        item.get('title',      '').strip(),
                        item.get('category',   '').strip(),
                        item.get('day_of_week','').strip(),
                        item.get('start_time', '').strip(),
                        item.get('end_time',   '').strip(),
                        item.get('notes',      '').strip(),
                    ),
                )

    def get_occupied_times_by_user_id(self, user_id: int) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT * FROM occupied_times
                WHERE user_id = ?
                ORDER BY day_of_week ASC, start_time ASC
                ''',
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ──────────────────────────────────────────────
    # Uploaded Files
    # ──────────────────────────────────────────────

    def insert_uploaded_file(
        self,
        user_id:           int,
        file_role:         str,
        original_filename: str,
        stored_filename:   str,
        file_path:         str,
        file_extension:    str,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO uploaded_files
                    (user_id, file_role, original_filename, stored_filename, file_path, file_extension)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (user_id, file_role, original_filename, stored_filename, file_path, file_extension.lower()),
            )
            return cursor.lastrowid

    def get_uploaded_files_by_user_id(self, user_id: int) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT * FROM uploaded_files
                WHERE user_id = ?
                ORDER BY uploaded_at DESC, uploaded_file_id DESC
                ''',
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_uploaded_files(self, user_id: int, uploaded_files: List[Dict]) -> None:
        """Full replace: delete all previous uploaded_file rows for the user, then insert the latest set."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM uploaded_files WHERE user_id = ?', (user_id,))
            for item in uploaded_files:
                conn.execute(
                    '''
                    INSERT INTO uploaded_files
                        (user_id, file_role, original_filename, stored_filename, file_path, file_extension)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        user_id,
                        item.get('file_role', '').strip(),
                        item.get('original_filename', '').strip(),
                        item.get('stored_filename', '').strip(),
                        item.get('file_path', '').strip(),
                        item.get('file_extension', '').strip().lower(),
                    ),
                )

    def get_latest_file_by_role(self, user_id: int, file_role: str) -> Optional[Dict]:
        """Return the most recently uploaded file for a given role (e.g. 'class_timetable')."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT * FROM uploaded_files
                WHERE user_id = ? AND file_role = ?
                ORDER BY uploaded_at DESC, uploaded_file_id DESC
                LIMIT 1
                ''',
                (user_id, file_role),
            ).fetchone()
        return dict(row) if row else None

    # ──────────────────────────────────────────────
    # Planning Agent Context
    # ──────────────────────────────────────────────

    def get_full_user_context(self, user_id: int) -> Optional[Dict]:
        """
        Return everything the planning agent needs for a user in one call.
        Returns None if no preferences have been saved yet (setup incomplete).
        """
        preferences = self.get_study_preferences_by_user_id(user_id)
        if preferences is None:
            return None

        all_files = self.get_uploaded_files_by_user_id(user_id)
        class_file = next(
            (f for f in all_files if f['file_role'] == 'class_timetable'), None
        )
        module_files = [f for f in all_files if f['file_role'] == 'module_timetable']

        return {
            'preferences':    preferences,
            'modules':        self.get_user_modules_by_user_id(user_id),
            'study_apps':     self.get_study_apps_by_user_id(user_id),
            'occupied_times': self.get_occupied_times_by_user_id(user_id),
            'files': {
                'class_timetable':   class_file,
                'module_timetables': module_files,
            },
        }

    def is_setup_complete(self, user_id: int) -> bool:
        """Quick check whether a user has finished the upload wizard."""
        prefs = self.get_study_preferences_by_user_id(user_id)
        if prefs is None:
            return False
        has_modules = bool(self.get_user_modules_by_user_id(user_id))
        has_files   = bool(self.get_latest_file_by_role(user_id, 'class_timetable'))
        return has_modules and has_files

    # ── Extraction Cache ───────────────────────────────────────────────────────

    def save_extraction_cache(self, user_id: int, result_json: str) -> None:
        """Upsert the extraction result JSON for a user."""
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO extraction_cache (user_id, result_json, cached_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    result_json = excluded.result_json,
                    cached_at   = CURRENT_TIMESTAMP
                ''',
                (user_id, result_json),
            )

    def get_extraction_cache(self, user_id: int) -> Optional[Dict]:
        """Return cached extraction entry or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT result_json, cached_at FROM extraction_cache WHERE user_id = ?',
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def clear_extraction_cache(self, user_id: int) -> None:
        with self._get_connection() as conn:
            conn.execute('DELETE FROM extraction_cache WHERE user_id = ?', (user_id,))

    def get_extraction_result_json(self, user_id: int) -> Optional[Dict]:
        """Return parsed extraction result JSON only, or None if no cache / invalid JSON."""
        cache = self.get_extraction_cache(user_id)
        if not cache:
            return None
        try:
            return json.loads(cache['result_json'])
        except Exception:
            return None

    def get_latest_upload_time(self, user_id: int) -> Optional[str]:
        """Return the most recent uploaded_at timestamp for a user's files."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT MAX(uploaded_at) AS latest FROM uploaded_files WHERE user_id = ?',
                (user_id,),
            ).fetchone()
        return row['latest'] if row else None

    # ── Study Plans ────────────────────────────────────────────────────────────

    def save_study_plan(
        self,
        user_id:        int,
        title:          str,
        plan_text:      str,
        timetable_json: list,
    ) -> int:
        """Insert a new generated study plan. Returns the new plan_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO study_plans (user_id, title, plan_text, timetable_json)
                VALUES (?, ?, ?, ?)
                ''',
                (user_id, title, plan_text, json.dumps(timetable_json)),
            )
            return cursor.lastrowid

    def get_study_plans_by_user_id(self, user_id: int) -> List[Dict]:
        """Return all plans for a user, most-recent first (metadata only, no plan_text)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT plan_id, user_id, title, created_at
                FROM study_plans
                WHERE user_id = ?
                ORDER BY created_at DESC, plan_id DESC
                ''',
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_study_plan_by_user_id(self, user_id: int) -> Optional[Dict]:
        """Return the user's newest saved study plan, including parsed timetable_json."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT * FROM study_plans
                WHERE user_id = ?
                ORDER BY created_at DESC, plan_id DESC
                LIMIT 1
                ''',
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result['timetable_json'] = json.loads(result.get('timetable_json') or '[]')
        except Exception:
            result['timetable_json'] = []
        return result

    def get_study_plan_by_id(self, plan_id: int) -> Optional[Dict]:
        """Return a full plan row including plan_text and parsed timetable_json."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM study_plans WHERE plan_id = ?',
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        r = dict(row)
        try:
            r['timetable_json'] = json.loads(r.get('timetable_json') or '[]')
        except Exception:
            r['timetable_json'] = []
        return r

    def delete_study_plan(self, plan_id: int) -> None:
        """Delete a study plan by its ID."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM study_plans WHERE plan_id = ?', (plan_id,))

    def update_study_plan_timetable(self, plan_id: int, new_timetable_json: list, append_text: str = "") -> None:
        """Update a plan's timetable and optionally append a note to the plan text."""
        timetable_str = json.dumps(new_timetable_json, ensure_ascii=False)
        with self._get_connection() as conn:
            if append_text:
                conn.execute(
                    '''
                    UPDATE study_plans
                    SET timetable_json = ?,
                        plan_text = plan_text || '\n\n### Replanning Note\n' || ?
                    WHERE plan_id = ?
                    ''',
                    (timetable_str, append_text, plan_id)
                )
                print(f"[DB] Updated plan {plan_id} with replanned timetable. Note: {append_text}")
            else:
                conn.execute(
                    'UPDATE study_plans SET timetable_json = ? WHERE plan_id = ?',
                    (timetable_str, plan_id)
                )
                print(f"[DB] Updated plan {plan_id} with new timetable.")
            # Verify the update
            row = conn.execute('SELECT timetable_json FROM study_plans WHERE plan_id = ?', (plan_id,)).fetchone()
            if row:
                print(f"[DB] Verified: Plan {plan_id} timetable length = {len(row[0])} chars")


    # ── Daily Summary Cards ───────────────────────────────────────────────────

    def get_all_active_user_ids(self) -> List[int]:
        """Return user_id of every user (used by the scheduler)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT user_id FROM users ORDER BY user_id'
            ).fetchall()
        return [row['user_id'] for row in rows]

    def get_all_daily_summary_cards(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Return ALL daily summary cards for a user, newest week first.
        Used by get_all_summary_history() to build the week→day tree.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT * FROM daily_summary_cards
                WHERE user_id = ?
                ORDER BY academic_week DESC, summary_date DESC, lesson_time ASC
                ''',
                (user_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['summary']    = _safe_json_loads(item.get('summary_json',   '{}'), {})
            item['resources']  = _safe_json_loads(item.get('resources_json', '[]'), [])
            item['flashcards'] = _safe_json_loads(item.get('flashcards_json','[]'), [])
            item['quiz_bank']  = _safe_json_loads(item.get('quiz_bank_json', '{}'), {})
            result.append(item)
        return result

    def get_daily_summary_cards_by_date(self, user_id: int, summary_date: str) -> List[Dict[str, Any]]:
        """Return persisted daily summary cards for one user and date."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT * FROM daily_summary_cards
                WHERE user_id = ? AND summary_date = ?
                ORDER BY lesson_time ASC, summary_id ASC
                ''',
                (user_id, summary_date),
            ).fetchall()
        cards: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item['summary']    = _safe_json_loads(item.get('summary_json',   '{}'), {})
            item['resources']  = _safe_json_loads(item.get('resources_json', '[]'), [])
            item['flashcards'] = _safe_json_loads(item.get('flashcards_json','[]'), [])
            item['quiz_bank']  = _safe_json_loads(item.get('quiz_bank_json', '{}'), {})
            cards.append(item)
        return cards

    def get_daily_summary_card(self, user_id: int, summary_id: int) -> Optional[Dict[str, Any]]:
        """Return one persisted summary card owned by the user."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT * FROM daily_summary_cards
                WHERE user_id = ? AND summary_id = ?
                LIMIT 1
                ''',
                (user_id, summary_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item['summary']    = _safe_json_loads(item.get('summary_json',   '{}'), {})
        item['resources']  = _safe_json_loads(item.get('resources_json', '[]'), [])
        item['flashcards'] = _safe_json_loads(item.get('flashcards_json','[]'), [])
        item['quiz_bank']  = _safe_json_loads(item.get('quiz_bank_json', '{}'), {})
        return item

    def upsert_daily_summary_card(
        self,
        *,
        user_id: int,
        summary_date: str,
        academic_week: int = 0,
        weekday: str,
        subject: str,
        topic: str,
        start_time: str = "",
        end_time: str = "",
        lesson_time: str,
        difficulty: str = "Medium",
        read_time: int = 3,
        prompt_count: int = 0,
        short_description: str = "",
        summary_payload: Dict[str, Any] = None,
        resources: List[Dict[str, Any]] = None,
        flashcards: List[Dict[str, Any]] = None,
        quiz_bank: Dict[str, Any] = None,
    ) -> int:
        """Insert or update one summary card and return its summary_id."""
        summary_json    = json.dumps(summary_payload or {}, ensure_ascii=False)
        resources_json  = json.dumps(resources  or [], ensure_ascii=False)
        flashcards_json = json.dumps(flashcards or [], ensure_ascii=False)
        quiz_bank_json  = json.dumps(quiz_bank  or {}, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO daily_summary_cards (
                    user_id, summary_date, academic_week, weekday,
                    subject, topic, start_time, end_time, lesson_time,
                    difficulty, read_time, prompt_count,
                    short_description, summary_json, resources_json,
                    flashcards_json, quiz_bank_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, summary_date, subject, topic, lesson_time) DO UPDATE SET
                    academic_week     = excluded.academic_week,
                    weekday           = excluded.weekday,
                    start_time        = excluded.start_time,
                    end_time          = excluded.end_time,
                    difficulty        = excluded.difficulty,
                    read_time         = excluded.read_time,
                    prompt_count      = excluded.prompt_count,
                    short_description = excluded.short_description,
                    summary_json      = excluded.summary_json,
                    resources_json    = excluded.resources_json,
                    flashcards_json   = excluded.flashcards_json,
                    quiz_bank_json    = excluded.quiz_bank_json,
                    updated_at        = CURRENT_TIMESTAMP
                ''',
                (
                    user_id, summary_date, academic_week, weekday,
                    subject, topic, start_time, end_time, lesson_time,
                    difficulty, int(read_time or 3), int(prompt_count or 0),
                    short_description, summary_json, resources_json,
                    flashcards_json, quiz_bank_json,
                ),
            )
            row = conn.execute(
                '''
                SELECT summary_id FROM daily_summary_cards
                WHERE user_id = ? AND summary_date = ? AND subject = ? AND topic = ? AND lesson_time = ?
                LIMIT 1
                ''',
                (user_id, summary_date, subject, topic, lesson_time),
            ).fetchone()

        return int(row['summary_id']) if row else 0

    def update_daily_summary_note(self, user_id: int, summary_id: int, note_text: str) -> bool:
        """Update note text for one summary card. Returns True if row exists."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''
                UPDATE daily_summary_cards
                SET note_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND summary_id = ?
                ''',
                ((note_text or '').strip(), user_id, summary_id),
            )
            return cursor.rowcount > 0

    def update_daily_summary_resources(self, user_id: int, summary_id: int, resources: List[Dict[str, Any]]) -> bool:
        """Update resources list for one summary card. Returns True if row exists."""
        resources_json = json.dumps(resources or [], ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''
                UPDATE daily_summary_cards
                SET resources_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND summary_id = ?
                ''',
                (resources_json, user_id, summary_id),
            )
            return cursor.rowcount > 0

    def save_flashcard_progress(self, user_id: int, summary_id: int, known_card_indices: List[int]) -> bool:
        """Save which flashcard indices the user marked as 'known'."""
        indices_json = json.dumps(known_card_indices or [], ensure_ascii=False)
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO flashcard_progress (user_id, summary_id, known_card_indices, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, summary_id) DO UPDATE SET
                    known_card_indices = excluded.known_card_indices,
                    updated_at = CURRENT_TIMESTAMP
                ''',
                (user_id, summary_id, indices_json),
            )
        return True

    def get_flashcard_progress(self, user_id: int, summary_id: int) -> List[int]:
        """Return list of flashcard indices marked as 'known' for a summary."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT known_card_indices FROM flashcard_progress
                WHERE user_id = ? AND summary_id = ?
                ''',
                (user_id, summary_id),
            ).fetchone()
        if not row:
            return []
        try:
            return json.loads(row['known_card_indices'] or '[]')
        except Exception:
            return []

    def save_quiz_result(
        self,
        user_id: int,
        summary_id: int,
        difficulty: str,
        question_count: int,
        correct_count: int,
        answers: List[Any],
    ) -> int:
        """Save a quiz attempt result. Returns result_id."""
        answers_json = json.dumps(answers or [], ensure_ascii=False)
        results_json = json.dumps(
            {"difficulty": difficulty, "correct": correct_count, "total": question_count},
            ensure_ascii=False,
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO quiz_results (
                    user_id, summary_id, difficulty, question_count,
                    correct_count, answers_json, results_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''',
                (user_id, summary_id, difficulty, question_count, correct_count, answers_json, results_json),
            )
            return cursor.lastrowid

    def get_quiz_results_for_summary(self, user_id: int, summary_id: int) -> List[Dict[str, Any]]:
        """Get all quiz results for a summary card."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT result_id, difficulty, question_count, correct_count, results_json, created_at
                FROM quiz_results
                WHERE user_id = ? AND summary_id = ?
                ORDER BY created_at DESC
                LIMIT 10
                ''',
                (user_id, summary_id),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item['results'] = _safe_json_loads(item.get('results_json', '{}'), {})
            results.append(item)
        return results

    def save_quiz_bank(self, user_id: int, summary_id: int, quiz_payload: Dict[str, Any]) -> int:
        """Insert or replace the on-demand quiz bank for a summary."""
        quiz_json = json.dumps(quiz_payload or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO quiz_banks (user_id, summary_id, quiz_json)
                VALUES (?, ?, ?)
                ON CONFLICT(summary_id) DO UPDATE SET
                    quiz_json  = excluded.quiz_json,
                    created_at = CURRENT_TIMESTAMP
                ''',
                (user_id, summary_id, quiz_json),
            )
            return cursor.lastrowid

    def get_quiz_bank(self, user_id: int, summary_id: int) -> Optional[Dict[str, Any]]:
        """Return the generated quiz bank for a summary, or None if not generated."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT quiz_json FROM quiz_banks
                WHERE user_id = ? AND summary_id = ?
                ''',
                (user_id, summary_id),
            ).fetchone()
        if not row:
            return None
        return _safe_json_loads(row['quiz_json'], {})

    def delete_quiz_bank(self, user_id: int, summary_id: int) -> bool:
        """Delete the quiz bank so it can be regenerated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''
                DELETE FROM quiz_banks
                WHERE user_id = ? AND summary_id = ?
                ''',
                (user_id, summary_id),
            )
            return cursor.rowcount > 0

    # ── Study Tracker ──────────────────────────────────────────────────────────

    def insert_study_session(self, data: Dict) -> int:
        """Insert a new study session row and return its session_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO study_sessions
                    (user_id, module_name, planned_duration_mins,
                     actual_start, actual_end, status,
                     study_seconds, inactivity_seconds, distraction_seconds,
                     current_app, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    data["user_id"],
                    data.get("module_name", ""),
                    data.get("planned_duration_mins", 60),
                    data.get("actual_start"),
                    data.get("actual_end"),
                    data.get("status", "not_started"),
                    data.get("study_seconds", 0),
                    data.get("inactivity_seconds", 0),
                    data.get("distraction_seconds", 0),
                    data.get("current_app", ""),
                    data.get("last_heartbeat"),
                ),
            )
            return cursor.lastrowid

    def update_session(self, data: Dict) -> None:
        """Update mutable fields of a study session by its session_id."""
        with self._get_connection() as conn:
            conn.execute(
                '''
                UPDATE study_sessions SET
                    status              = ?,
                    study_seconds       = ?,
                    inactivity_seconds  = ?,
                    distraction_seconds = ?,
                    current_app         = ?,
                    actual_start        = ?,
                    actual_end          = ?,
                    last_heartbeat      = ?
                WHERE session_id = ?
                ''',
                (
                    data.get("status", "active"),
                    data.get("study_seconds", 0),
                    data.get("inactivity_seconds", 0),
                    data.get("distraction_seconds", 0),
                    data.get("current_app", ""),
                    data.get("actual_start"),
                    data.get("actual_end"),
                    data.get("last_heartbeat"),
                    data["session_id"],
                ),
            )

    def get_session_by_id(self, session_id: int) -> Optional[Dict]:
        """Return a single study session row or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM study_sessions WHERE session_id = ?',
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def clear_session_history(self, user_id: int) -> None:
        """Delete all non-active sessions for a user."""
        with self._get_connection() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT session_id FROM study_sessions WHERE user_id=? AND status NOT IN ('active','warning','not_started')",
                (user_id,)
            ).fetchall()]
            for sid in ids:
                conn.execute('DELETE FROM session_events WHERE session_id=?', (sid,))
                conn.execute('DELETE FROM study_sessions WHERE session_id=?', (sid,))

    def delete_session(self, session_id: int) -> None:
        """Permanently delete a study session and its events."""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM session_events  WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM study_sessions  WHERE session_id = ?', (session_id,))

    def get_active_session(self, user_id: int) -> Optional[Dict]:
        """Return the most recent non-terminal session for a user, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT * FROM study_sessions
                WHERE user_id = ? AND status IN ('active', 'warning', 'not_started')
                ORDER BY created_at DESC, session_id DESC
                LIMIT 1
                ''',
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_recent_sessions(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Return up to `limit` most-recent finished sessions (excludes active/in_progress)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT * FROM study_sessions
                WHERE user_id = ?
                  AND status NOT IN ('active', 'warning', 'not_started')
                ORDER BY created_at DESC, session_id DESC
                LIMIT ?
                ''',
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def session_exists_for_slot(
        self,
        user_id: int,
        module_name: str,
        date_str: str,
        slot_start: str = "",
        slot_end: str = "",
    ) -> bool:
        """Return True if a live (non-incompleted) session exists for this specific time slot."""
        with self._get_connection() as conn:
            if slot_start and slot_end:
                row = conn.execute(
                    '''
                    SELECT 1 FROM study_sessions
                    WHERE user_id    = ?
                      AND module_name = ?
                      AND DATE(COALESCE(actual_start, created_at)) = ?
                      AND status != 'incompleted'
                      AND time(COALESCE(actual_start, created_at)) >= ?
                      AND time(COALESCE(actual_start, created_at)) <  ?
                    LIMIT 1
                    ''',
                    (user_id, module_name, date_str, slot_start + ":00", slot_end + ":00"),
                ).fetchone()
            else:
                row = conn.execute(
                    '''
                    SELECT 1 FROM study_sessions
                    WHERE user_id    = ?
                      AND module_name = ?
                      AND DATE(COALESCE(actual_start, created_at)) = ?
                      AND status != 'incompleted'
                    LIMIT 1
                    ''',
                    (user_id, module_name, date_str),
                ).fetchone()
        return row is not None

    def session_exists_for_slot_any(
        self,
        user_id: int,
        module_name: str,
        date_str: str,
        slot_start: str = "",
        slot_end: str = "",
    ) -> bool:
        """Return True if ANY session (including incompleted) exists for this specific slot."""
        with self._get_connection() as conn:
            if slot_start and slot_end:
                row = conn.execute(
                    '''
                    SELECT 1 FROM study_sessions
                    WHERE user_id    = ?
                      AND module_name = ?
                      AND DATE(COALESCE(actual_start, created_at)) = ?
                      AND time(COALESCE(actual_start, created_at)) >= ?
                      AND time(COALESCE(actual_start, created_at)) <  ?
                    LIMIT 1
                    ''',
                    (user_id, module_name, date_str, slot_start + ":00", slot_end + ":00"),
                ).fetchone()
            else:
                row = conn.execute(
                    '''
                    SELECT 1 FROM study_sessions
                    WHERE user_id    = ?
                      AND module_name = ?
                      AND DATE(COALESCE(actual_start, created_at)) = ?
                    LIMIT 1
                    ''',
                    (user_id, module_name, date_str),
                ).fetchone()
        return row is not None

    def log_session_event(
        self,
        session_id: int,
        user_id:    int,
        event_type: str,
        details:    str = "",
    ) -> None:
        """Append an event to the session_events log."""
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO session_events (session_id, user_id, event_type, details)
                VALUES (?, ?, ?, ?)
                ''',
                (session_id, user_id, event_type, details),
            )

    def claim_session_result_notification(self, session_id: int, user_id: int) -> bool:
        """Atomically allow only one client to announce a session's final result."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO session_events (session_id, user_id, event_type, details)
                SELECT session_id, user_id, 'result_notification', status
                FROM study_sessions
                WHERE session_id = ? AND user_id = ?
                  AND status IN ('completed', 'incompleted')
                  AND NOT EXISTS (
                      SELECT 1 FROM session_events
                      WHERE session_id = ? AND event_type = 'result_notification'
                  )
                """,
                (session_id, user_id, session_id),
            )
            return cursor.rowcount == 1

    def is_session_replanned(self, session_id: int) -> bool:
        """Return True if this session has been successfully replanned."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM session_events WHERE session_id = ? AND event_type = 'replanned' LIMIT 1",
                (session_id,)
            ).fetchone()
        return row is not None


    # ── Monitor Tokens ─────────────────────────────────────────────────────────

    def create_monitor_token(self, user_id: int, ttl_hours: int = 24) -> str:
        """Generate a monitor auth token for the given user without revoking active tokens."""
        import secrets
        from datetime import datetime as _dt, timedelta
        now_iso = _dt.now().isoformat(timespec="seconds")
        token = secrets.token_urlsafe(24)
        expires_at = (_dt.now() + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute("DELETE FROM monitor_tokens WHERE expires_at < ?", (now_iso,))
            conn.execute(
                "INSERT INTO monitor_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user_id, expires_at),
            )
        return token

    def get_or_create_monitor_token(self, user_id: int, ttl_hours: int = 24 * 365 * 10) -> str:
        """Return an existing valid monitor token for user_id, or create one if none exists."""
        from datetime import datetime as _dt, timedelta
        now = _dt.now()
        now_iso = now.isoformat(timespec="seconds")
        new_expires_at = (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute("DELETE FROM monitor_tokens WHERE expires_at < ?", (now_iso,))
            row = conn.execute(
                "SELECT token, expires_at FROM monitor_tokens WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if row and row["token"]:
                try:
                    if _dt.fromisoformat(row["expires_at"]) > now:
                        conn.execute(
                            "UPDATE monitor_tokens SET expires_at = ? WHERE token = ?",
                            (new_expires_at, row["token"]),
                        )
                        return row["token"]
                except Exception:
                    pass
        return self.create_monitor_token(user_id, ttl_hours=ttl_hours)

    # ── Coach Recall Functionality ─────────────────────────────────────────────

    def get_pending_recall_summaries(self, user_id: int) -> List[Dict[str, Any]]:
        """Fetch summaries created 7-14 days ago that haven't been recalled yet."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT s.summary_id, s.summary_date, s.subject, s.topic, s.summary_json
                FROM daily_summary_cards s
                LEFT JOIN coach_recalls r ON s.summary_id = r.summary_id
                WHERE s.user_id = ? 
                  AND r.summary_id IS NULL
                  AND (julianday('now') - julianday(s.created_at)) >= 7
                  AND (julianday('now') - julianday(s.created_at)) <= 14
                ORDER BY s.created_at ASC
                LIMIT 5
                ''',
                (user_id,)
            ).fetchall()
            
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item['summary_json'] = json.loads(row['summary_json'])
                except Exception:
                    item['summary_json'] = {}
                result.append(item)
            return result

    def log_coach_recall(self, user_id: int, summary_id: int) -> None:
        """Mark a summary as having received a recall prompt."""
        with self._get_connection() as conn:
            conn.execute(
                '''
                INSERT OR IGNORE INTO coach_recalls (user_id, summary_id)
                VALUES (?, ?)
                ''',
                (user_id, summary_id)
            )

    # Assignment Checker History

    def save_assignment_history(
        self,
        user_id: int,
        title: str,
        strictness: str,
        rubric_mode: bool,
        overall_score: Optional[int],
        grade: Optional[str],
        total_marks_awarded: Optional[float],
        total_marks_possible: Optional[float],
        submission_snippet: str,
        brief_snippet: str,
        result_json: str,
    ) -> int:
        """Insert a new assignment check record and return its history_id."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                '''
                INSERT INTO assignment_checker_history
                    (user_id, title, strictness, rubric_mode, overall_score, grade,
                     total_marks_awarded, total_marks_possible,
                     submission_snippet, brief_snippet, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    user_id, title, strictness, 1 if rubric_mode else 0,
                    overall_score, grade,
                    total_marks_awarded, total_marks_possible,
                    submission_snippet, brief_snippet, result_json,
                ),
            )
            return cursor.lastrowid

    def get_assignment_history(self, user_id: int, limit: int = 50) -> List[Dict]:
        """Return up to `limit` history entries for a user, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT history_id, user_id, created_at, title, strictness,
                       rubric_mode, overall_score, grade,
                       total_marks_awarded, total_marks_possible,
                       submission_snippet, brief_snippet
                FROM assignment_checker_history
                WHERE user_id = ?
                ORDER BY created_at DESC, history_id DESC
                LIMIT ?
                ''',
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_assignment_history_entry(self, history_id: int, user_id: int) -> Optional[Dict]:
        """Return a single history entry including full result_json, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                '''
                SELECT * FROM assignment_checker_history
                WHERE history_id = ? AND user_id = ?
                ''',
                (history_id, user_id),
            ).fetchone()
        if not row:
            return None
        entry = dict(row)
        entry['result'] = _safe_json_loads(entry.get('result_json', '{}'), {})
        return entry

    def delete_assignment_history_entry(self, history_id: int, user_id: int) -> None:
        """Delete a single history entry, scoped to the owning user."""
        with self._get_connection() as conn:
            conn.execute(
                'DELETE FROM assignment_checker_history WHERE history_id = ? AND user_id = ?',
                (history_id, user_id),
            )

    def get_user_id_from_token(self, token: str) -> Optional[int]:
        """Return user_id for a valid non-expired token, or None."""
        from datetime import datetime as _dt
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM monitor_tokens WHERE token = ?",
                (token,),
            ).fetchone()
        if not row:
            return None
        try:
            if _dt.fromisoformat(row["expires_at"]) < _dt.now():
                return None
        except Exception:
            pass
        return row["user_id"]

    # ── Reflection Stats ───────────────────────────────────────────────────────────

    def get_reflection_week_stats(self, user_id: int, week_number: int) -> Dict:
        """
        Compute the four stat-card values for the given academic week.

        Planned hours     → total (end - start) minutes from timetable slots, in hours.
        Completed hours   → sum of study_seconds where status='completed' in the week.
        Missed sessions   → count of rows where status='incompleted' in the week.
        Completion rate   → completed_hours / planned_hours * 100.
        """
        from datetime import datetime as _dt, timedelta as _td

        # ── 1. Date range for the week ─────────────────────────────────────────────
        prefs = self.get_study_preferences_by_user_id(user_id)
        plan_week_start: Optional[str] = None
        plan_week_end:   Optional[str] = None

        if prefs and prefs.get('semester_start_date'):
            try:
                sem_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
                week_start_dt = sem_start + _td(weeks=week_number - 1)
                week_end_dt   = week_start_dt + _td(days=6)
                plan_week_start = week_start_dt.strftime('%Y-%m-%d')
                plan_week_end   = week_end_dt.strftime('%Y-%m-%d')
            except Exception:
                pass

        # ── 2. Planned hours: find the study plan for this week ────────────────────
        #
        # Week number is encoded in the plan *title* (e.g. "Week 4 Study Plan"),
        # NOT inside the timetable slot JSON.  We pick the latest saved plan whose
        # title matches the requested week_number, then sum its slot durations.
        planned_mins_total = 0
        sessions_scheduled = 0

        import re as _re

        def _title_week(title: str) -> Optional[int]:
            m = _re.search(r'(?i)\bweek\s*(\d+)\b', title or '')
            if not m:
                return None
            try:
                return int(m.group(1))
            except Exception:
                return None

        with self._get_connection() as conn:
            plan_rows = conn.execute(
                'SELECT plan_id, title, timetable_json FROM study_plans'
                ' WHERE user_id = ? ORDER BY created_at DESC, plan_id DESC',
                (user_id,),
            ).fetchall()

        # Keep only the newest plan whose title week matches the requested week
        matched_timetable = None
        for row in plan_rows:
            if _title_week(row['title']) == week_number:
                matched_timetable = _safe_json_loads(row['timetable_json'], [])
                break

        if matched_timetable and isinstance(matched_timetable, list):
            for slot in matched_timetable:
                try:
                    sh, sm = map(int, slot.get('start', '0:0').split(':'))
                    eh, em = map(int, slot.get('end',   '0:0').split(':'))
                    dur = (eh * 60 + em) - (sh * 60 + sm)
                    if dur > 0:
                        planned_mins_total += dur
                        sessions_scheduled += 1
                except Exception:
                    pass

        planned_hours = round(planned_mins_total / 60, 1)

        # ── 3. Completed & missed sessions in the week's date range ───────────────
        completed_secs = 0
        sessions_done  = 0
        missed_count   = 0

        if plan_week_start and plan_week_end:
            with self._get_connection() as conn:
                comp_rows = conn.execute(
                    '''
                    SELECT study_seconds FROM study_sessions
                    WHERE user_id = ?
                      AND status = 'completed'
                      AND DATE(COALESCE(actual_start, created_at)) BETWEEN ? AND ?
                    ''',
                    (user_id, plan_week_start, plan_week_end),
                ).fetchall()
                for r in comp_rows:
                    completed_secs += (r['study_seconds'] or 0)
                    sessions_done  += 1

                # Get count and total planned duration of missed sessions
                missed_row = conn.execute(
                    '''
                    SELECT
                        COUNT(*) AS cnt,
                        SUM(planned_duration_mins) AS total_planned_mins
                    FROM study_sessions
                    WHERE user_id = ?
                      AND status = 'incompleted'
                      AND DATE(COALESCE(actual_start, created_at)) BETWEEN ? AND ?
                    ''',
                    (user_id, plan_week_start, plan_week_end),
                ).fetchone()
                missed_count = missed_row['cnt'] if missed_row else 0
                missed_mins = missed_row['total_planned_mins'] if missed_row and missed_row['total_planned_mins'] else 0

        completed_hours = round(completed_secs / 3600, 1)
        missed_hours    = round(missed_mins / 60, 1)

        # ── 4. Completion rate ─────────────────────────────────────────────────────
        if planned_hours > 0:
            completion_rate = min(100, round((completed_hours / planned_hours) * 100))
        else:
            completion_rate = 0

        return {
            'planned_hours':      planned_hours,
            'sessions_scheduled': sessions_scheduled,
            'completed_hours':    completed_hours,
            'sessions_done':      sessions_done,
            'missed_sessions':    missed_count,
            'missed_hours':       missed_hours,
            'completion_rate':    completion_rate,
        }

    def get_reflection_module_stats(self, user_id: int, week_number: int) -> List[Dict]:
        """
        Aggregate study session metrics by module for the requested academic week.
        Shows ALL modules from the study plan with their planned hours and actual completion status.
        """
        from datetime import datetime as _dt, timedelta as _td
        import re as _re

        prefs = self.get_study_preferences_by_user_id(user_id)
        if not prefs or not prefs.get('semester_start_date'):
            return []

        try:
            sem_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
            week_start_dt = sem_start + _td(weeks=week_number - 1)
            week_end_dt = week_start_dt + _td(days=6)
            week_start = week_start_dt.strftime('%Y-%m-%d')
            week_end = week_end_dt.strftime('%Y-%m-%d')
        except Exception:
            return []

        # ── Step 1: Get planned hours per module from study_plan timetable_json ────
        def _title_week(title: str) -> Optional[int]:
            m = _re.search(r'(?i)\bweek\s*(\d+)\b', title or '')
            if not m:
                return None
            try:
                return int(m.group(1))
            except Exception:
                return None

        planned_by_module: Dict[str, float] = {}  # module_name -> planned hours

        with self._get_connection() as conn:
            plan_rows = conn.execute(
                'SELECT title, timetable_json FROM study_plans'
                ' WHERE user_id = ? ORDER BY created_at DESC, plan_id DESC',
                (user_id,),
            ).fetchall()

        # Find the matching plan for this week
        matched_timetable = None
        for row in plan_rows:
            if _title_week(row['title']) == week_number:
                matched_timetable = _safe_json_loads(row['timetable_json'], [])
                break

        if matched_timetable and isinstance(matched_timetable, list):
            for slot in matched_timetable:
                try:
                    subject = (slot.get('subject') or 'Unnamed Module').strip()
                    if not subject:
                        subject = 'Unnamed Module'

                    # Calculate slot duration in hours
                    sh, sm = map(int, slot.get('start', '0:0').split(':'))
                    eh, em = map(int, slot.get('end', '0:0').split(':'))
                    dur_mins = (eh * 60 + em) - (sh * 60 + sm)

                    if dur_mins > 0:
                        planned_by_module[subject] = planned_by_module.get(subject, 0) + (dur_mins / 60)
                except Exception:
                    pass

        # ── Step 2: Get completed hours per module from study_sessions ─────────────
        completed_by_module: Dict[str, Dict[str, Any]] = {}  # module_name -> {completed_hours, sessions, ...}

        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT
                    COALESCE(NULLIF(TRIM(module_name), ''), 'Unnamed Module') AS module_name,
                    SUM(CASE WHEN status = 'completed' THEN study_seconds ELSE 0 END) AS completed_secs,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_sessions,
                    COUNT(*) AS total_sessions
                FROM study_sessions
                WHERE user_id = ?
                  AND DATE(COALESCE(actual_start, created_at)) BETWEEN ? AND ?
                GROUP BY module_name
                ''',
                (user_id, week_start, week_end),
            ).fetchall()

        for row in rows:
            module_name = row['module_name']
            completed_by_module[module_name] = {
                'completed_hours': round((row['completed_secs'] or 0) / 3600, 1),
                'completed_sessions': row['completed_sessions'] or 0,
                'total_sessions': row['total_sessions'] or 0,
            }

        # ── Step 3: Combine data for all planned modules ───────────────────────────
        module_stats: List[Dict] = []

        for module_name, planned_hours in planned_by_module.items():
            completed_data = completed_by_module.get(module_name, {
                'completed_hours': 0.0,
                'completed_sessions': 0,
                'total_sessions': 0,
            })

            planned_hours_rounded = round(planned_hours, 1)
            completed_hours = completed_data['completed_hours']

            completion_rate = 0
            if planned_hours_rounded > 0:
                completion_rate = min(100, round((completed_hours / planned_hours_rounded) * 100))

            module_stats.append({
                'module_name': module_name,
                'planned_hours': planned_hours_rounded,
                'completed_hours': completed_hours,
                'completion_rate': completion_rate,
                'completed_sessions': completed_data['completed_sessions'],
                'total_sessions': completed_data['total_sessions'],
            })

        # Sort by planned hours (descending), then by completion rate (descending)
        module_stats.sort(key=lambda item: (item['planned_hours'], item['completion_rate']), reverse=True)
        return module_stats

    def get_daily_hours_for_week(self, user_id: int, week_number: int) -> List[Dict]:
        """
        Return planned and completed study hours for each day of the given academic week.

        Each item in the returned list represents one day (Mon–Sun):
          {
            'day':       'Mon',          # short label
            'planned':   2.0,            # hours from matching timetable_json
            'completed': 1.5,            # hours from study_sessions (status='completed')
          }
        """
        from datetime import datetime as _dt, timedelta as _td
        import re as _re

        DAYS_SHORT  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        DAYS_FULL   = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

        # ── Date range for the week ────────────────────────────────────────────────
        prefs = self.get_study_preferences_by_user_id(user_id)
        week_dates: list[str] = []          # ISO date strings for Mon … Sun
        if prefs and prefs.get('semester_start_date'):
            try:
                sem_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
                week_start_dt = sem_start + _td(weeks=week_number - 1)
                week_dates = [
                    (week_start_dt + _td(days=i)).strftime('%Y-%m-%d')
                    for i in range(7)
                ]
            except Exception:
                pass

        # ── Planned hours: find matching study plan timetable ──────────────────────
        def _title_week(title: str):
            m = _re.search(r'(?i)\bweek\s*(\d+)\b', title or '')
            if not m:
                return None
            try:
                return int(m.group(1))
            except Exception:
                return None

        planned_by_day: dict[int, float] = {i: 0.0 for i in range(7)}   # 0=Mon … 6=Sun

        with self._get_connection() as conn:
            plan_rows = conn.execute(
                'SELECT title, timetable_json FROM study_plans'
                ' WHERE user_id = ? ORDER BY created_at DESC, plan_id DESC',
                (user_id,),
            ).fetchall()

        matched_timetable = None
        for row in plan_rows:
            if _title_week(row['title']) == week_number:
                matched_timetable = _safe_json_loads(row['timetable_json'], [])
                break

        if matched_timetable and isinstance(matched_timetable, list):
            for slot in matched_timetable:
                day_raw = (slot.get('day') or '').strip()
                # Normalise: accept full day names (Monday) or short (Mon)
                day_idx = None
                if day_raw in DAYS_FULL:
                    day_idx = DAYS_FULL.index(day_raw)
                elif day_raw in DAYS_SHORT:
                    day_idx = DAYS_SHORT.index(day_raw)
                if day_idx is None:
                    continue
                try:
                    sh, sm = map(int, slot.get('start', '0:0').split(':'))
                    eh, em = map(int, slot.get('end',   '0:0').split(':'))
                    dur_mins = (eh * 60 + em) - (sh * 60 + sm)
                    if dur_mins > 0:
                        planned_by_day[day_idx] += dur_mins / 60
                except Exception:
                    pass

        # ── Completed hours: sum study_seconds per calendar day ────────────────────
        completed_by_day: dict[int, float] = {i: 0.0 for i in range(7)}

        if week_dates:
            week_start_iso = week_dates[0]
            week_end_iso   = week_dates[-1]
            with self._get_connection() as conn:
                comp_rows = conn.execute(
                    '''
                    SELECT
                        DATE(COALESCE(actual_start, created_at)) AS study_date,
                        SUM(study_seconds) AS total_secs
                    FROM study_sessions
                    WHERE user_id = ?
                      AND status = 'completed'
                      AND DATE(COALESCE(actual_start, created_at)) BETWEEN ? AND ?
                    GROUP BY DATE(COALESCE(actual_start, created_at))
                    ''',
                    (user_id, week_start_iso, week_end_iso),
                ).fetchall()

            for r in comp_rows:
                date_str = r['study_date']
                if date_str in week_dates:
                    day_idx = week_dates.index(date_str)
                    completed_by_day[day_idx] += (r['total_secs'] or 0) / 3600

        # ── Build result list ──────────────────────────────────────────────────────
        return [
            {
                'day':       DAYS_SHORT[i],
                'planned':   round(planned_by_day[i], 1),
                'completed': round(completed_by_day[i], 1),
            }
            for i in range(7)
        ]

    def get_reflection_study_patterns(self, user_id: int, week_number: int) -> Dict[str, Any]:
        """
        Analyze study session patterns for the reflection page:
        1. Peak Performance - time slot with highest completion rate
        2. Average Duration - average length of consecutive completed session sequences
        3. Most Productive - day with most completed sessions
        4. Struggle Period - time slot with most incomplete sessions
        """
        from datetime import datetime as _dt, timedelta as _td

        prefs = self.get_study_preferences_by_user_id(user_id)
        if not prefs or not prefs.get('semester_start_date'):
            return self._empty_study_patterns()

        try:
            sem_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
            week_start_dt = sem_start + _td(weeks=week_number - 1)
            week_end_dt = week_start_dt + _td(days=6)
            week_start = week_start_dt.strftime('%Y-%m-%d')
            week_end = week_end_dt.strftime('%Y-%m-%d')
        except Exception:
            return self._empty_study_patterns()

        # Fetch all sessions for the week, ordered by time
        with self._get_connection() as conn:
            sessions = conn.execute(
                '''
                SELECT
                    session_id,
                    module_name,
                    planned_duration_mins,
                    actual_start,
                    actual_end,
                    status,
                    study_seconds,
                    created_at
                FROM study_sessions
                WHERE user_id = ?
                  AND DATE(COALESCE(actual_start, created_at)) BETWEEN ? AND ?
                ORDER BY COALESCE(actual_start, created_at)
                ''',
                (user_id, week_start, week_end),
            ).fetchall()

        if not sessions:
            return self._empty_study_patterns()

        # Convert to list of dicts for easier processing
        sessions_list = [dict(s) for s in sessions]

        # Calculate all 4 patterns
        peak_performance = self._calculate_peak_performance(sessions_list)
        average_duration = self._calculate_average_duration(sessions_list)
        most_productive = self._calculate_most_productive(sessions_list)
        struggle_period = self._calculate_struggle_period(sessions_list)

        return {
            'peak_performance': peak_performance,
            'average_duration': average_duration,
            'most_productive': most_productive,
            'struggle_period': struggle_period,
        }

    def _empty_study_patterns(self) -> Dict[str, Any]:
        """Return empty/default study patterns when no data available."""
        return {
            'peak_performance': {
                'time_slot': 'No data',
                'period': '',
                'completion_rate': 0
            },
            'average_duration': {
                'minutes': 0,
                'target_minutes': 90,
                'formatted': '0min'
            },
            'most_productive': {
                'day': 'No data',
                'completed_sessions': 0
            },
            'struggle_period': {
                'time_slot': 'No data',
                'period': '',
                'missed_sessions': 0
            }
        }

    def _get_time_period(self, time_str: str) -> tuple[str, int]:
        """
        Extract hour from time string and return (period_name, hour).
        Periods: Morning (6-12), Afternoon (12-18), Evening (18-24), Night (0-6)
        """
        from datetime import datetime as _dt

        if not time_str:
            return ('Unknown', 12)

        try:
            # Handle multiple datetime formats
            # SQLite returns: 2026-04-27T17:15:00 or 2026-04-27 17:15:00
            time_str = time_str.replace('T', ' ')  # Normalize ISO format

            if ' ' in time_str:
                dt = _dt.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            else:
                dt = _dt.strptime(time_str, '%H:%M:%S')
            hour = dt.hour
        except Exception:
            return ('Unknown', 12)

        if 6 <= hour < 12:
            return ('Morning', hour)
        elif 12 <= hour < 18:
            return ('Afternoon', hour)
        elif 18 <= hour < 24:
            return ('Evening', hour)
        else:
            return ('Night', hour)

    def _calculate_peak_performance(self, sessions: List[Dict]) -> Dict[str, Any]:
        """Find time slot with highest completion rate."""
        from datetime import datetime as _dt

        # Group sessions by time period
        period_stats = {
            'Morning': {'completed': 0, 'total': 0, 'times': []},
            'Afternoon': {'completed': 0, 'total': 0, 'times': []},
            'Evening': {'completed': 0, 'total': 0, 'times': []},
            'Night': {'completed': 0, 'total': 0, 'times': []},
        }

        for session in sessions:
            time_str = session.get('actual_start') or session.get('created_at')
            period, hour = self._get_time_period(time_str)

            if period in period_stats:
                period_stats[period]['total'] += 1
                if session['status'] == 'completed':
                    period_stats[period]['completed'] += 1
                    period_stats[period]['times'].append(hour)

        # Find period with highest completion rate
        best_period = None
        best_rate = -1

        for period, stats in period_stats.items():
            if stats['total'] > 0:
                rate = (stats['completed'] / stats['total']) * 100
                if rate > best_rate:
                    best_rate = rate
                    best_period = period

        if not best_period or best_rate == 0:
            return {
                'time_slot': 'No completed sessions',
                'period': '',
                'completion_rate': 0
            }

        # Find actual time range from completed sessions
        times = period_stats[best_period]['times']
        if times:
            min_hour = min(times)
            max_hour = max(times)
            time_slot = f"{min_hour}:00 - {max_hour + 1}:00"

            # Format AM/PM
            if min_hour < 12:
                time_slot = f"{min_hour if min_hour > 0 else 12}:00"
                if max_hour < 12:
                    time_slot += f" - {max_hour + 1}:00 AM"
                else:
                    time_slot += " AM - " + f"{max_hour - 11}:00 PM" if max_hour > 12 else "12:00 PM"
            else:
                start = f"{min_hour - 12 if min_hour > 12 else 12}:00 PM"
                end = f"{(max_hour + 1) - 12 if max_hour >= 12 else max_hour + 1}:00"
                end += " PM" if max_hour >= 11 else " AM"
                time_slot = f"{start} - {end}"
        else:
            time_slot = best_period

        return {
            'time_slot': time_slot,
            'period': best_period,
            'completion_rate': round(best_rate)
        }

    def _calculate_average_duration(self, sessions: List[Dict]) -> Dict[str, Any]:
        """Calculate average duration of consecutive completed session sequences."""

        # Find consecutive sequences of completed sessions
        sequences = []
        current_sequence_duration = 0

        for session in sessions:
            if session['status'] == 'completed':
                # Add to current sequence
                current_sequence_duration += session['study_seconds']
            else:
                # Sequence broken, save if we had one
                if current_sequence_duration > 0:
                    sequences.append(current_sequence_duration)
                    current_sequence_duration = 0

        # Don't forget last sequence
        if current_sequence_duration > 0:
            sequences.append(current_sequence_duration)

        # Calculate average
        if not sequences:
            avg_seconds = 0
        else:
            avg_seconds = sum(sequences) / len(sequences)

        avg_minutes = round(avg_seconds / 60)

        # Get target from average planned duration
        target_minutes = 90  # default
        completed_sessions = [s for s in sessions if s['status'] == 'completed']
        if completed_sessions:
            total_planned = sum(s['planned_duration_mins'] for s in completed_sessions)
            target_minutes = round(total_planned / len(completed_sessions))

        # Format as "Xh Ymin"
        hours = avg_minutes // 60
        mins = avg_minutes % 60
        if hours > 0:
            formatted = f"{hours}h {mins}min" if mins > 0 else f"{hours}h"
        else:
            formatted = f"{mins}min"

        return {
            'minutes': avg_minutes,
            'target_minutes': target_minutes,
            'formatted': formatted,
            'sequences_count': len(sequences)
        }

    def _calculate_most_productive(self, sessions: List[Dict]) -> Dict[str, Any]:
        """Find day of week with most completed sessions."""
        from datetime import datetime as _dt

        DAYS_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

        # Count completed sessions by day
        day_counts = {day: 0 for day in DAYS_FULL}

        for session in sessions:
            if session['status'] == 'completed':
                time_str = session.get('actual_start') or session.get('created_at')
                try:
                    # Handle ISO format (2026-04-16T20:35:00) or space format
                    date_part = time_str.replace('T', ' ').split()[0]
                    dt = _dt.strptime(date_part, '%Y-%m-%d')
                    day_name = DAYS_FULL[dt.weekday()]
                    day_counts[day_name] += 1
                except Exception:
                    continue

        # Find day with most completions
        best_day = max(day_counts.items(), key=lambda x: x[1])

        if best_day[1] == 0:
            return {
                'day': 'No completed sessions',
                'completed_sessions': 0
            }

        return {
            'day': best_day[0],
            'completed_sessions': best_day[1]
        }

    def _calculate_struggle_period(self, sessions: List[Dict]) -> Dict[str, Any]:
        """Find time slot with most incomplete sessions."""

        # Group incomplete sessions by time period
        period_missed = {
            'Morning': {'count': 0, 'times': []},
            'Afternoon': {'count': 0, 'times': []},
            'Evening': {'count': 0, 'times': []},
            'Night': {'count': 0, 'times': []},
        }

        for session in sessions:
            if session['status'] != 'completed':
                time_str = session.get('actual_start') or session.get('created_at')
                period, hour = self._get_time_period(time_str)

                if period in period_missed:
                    period_missed[period]['count'] += 1
                    period_missed[period]['times'].append(hour)

        # Find period with most missed sessions
        worst_period = max(period_missed.items(), key=lambda x: x[1]['count'])

        if worst_period[1]['count'] == 0:
            return {
                'time_slot': 'No missed sessions',
                'period': '',
                'missed_sessions': 0
            }

        # Format time slot
        period_name = worst_period[0]
        times = worst_period[1]['times']
        missed_count = worst_period[1]['count']

        if times:
            min_hour = min(times)
            max_hour = max(times)

            # Format AM/PM
            if min_hour < 12:
                time_slot = f"{min_hour if min_hour > 0 else 12}:00"
                if max_hour < 12:
                    time_slot += f" - {max_hour + 1}:00 AM"
                else:
                    time_slot += " AM - " + (f"{max_hour - 11}:00 PM" if max_hour > 12 else "12:00 PM")
            else:
                start = f"{min_hour - 12 if min_hour > 12 else 12}:00 PM"
                end_hour = max_hour + 1
                end = f"{end_hour - 12 if end_hour > 12 else end_hour}:00"
                end += " PM" if end_hour >= 12 else " AM"
                time_slot = f"{start} - {end}"
        else:
            time_slot = period_name

        return {
            'time_slot': time_slot,
            'period': period_name,
            'missed_sessions': missed_count
        }

    def get_reflection_performance_highlights(self, user_id: int, week_number: int) -> Dict[str, Any]:
        """
        Get performance highlights based on quiz results for the week.
        Returns strongest module (highest avg quiz score) and module needing attention (lowest avg).

        Note: This uses ALL quiz attempts taken for lessons from this week,
        regardless of when the quiz was actually taken.
        """
        from datetime import datetime as _dt, timedelta as _td

        prefs = self.get_study_preferences_by_user_id(user_id)
        if not prefs or not prefs.get('semester_start_date'):
            return self._empty_performance_highlights()

        try:
            sem_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
            week_start_dt = sem_start + _td(weeks=week_number - 1)
            week_end_dt = week_start_dt + _td(days=6)
            week_start = week_start_dt.strftime('%Y-%m-%d')
            week_end = week_end_dt.strftime('%Y-%m-%d')
        except Exception:
            return self._empty_performance_highlights()

        # Get quiz results for lessons from this week (by summary_date, not quiz creation time)
        # This ensures quizzes taken later still count towards the week the lesson was from
        with self._get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT
                    s.subject AS module_name,
                    AVG(CAST(q.correct_count AS FLOAT) / CAST(q.question_count AS FLOAT) * 100) AS avg_score,
                    COUNT(q.result_id) AS quiz_count,
                    SUM(q.correct_count) AS total_correct,
                    SUM(q.question_count) AS total_questions
                FROM quiz_results q
                JOIN daily_summary_cards s ON q.summary_id = s.summary_id
                WHERE q.user_id = ?
                  AND s.summary_date BETWEEN ? AND ?
                  AND q.question_count > 0
                GROUP BY s.subject
                HAVING COUNT(q.result_id) > 0
                ORDER BY avg_score DESC
                ''',
                (user_id, week_start, week_end),
            ).fetchall()

        print(f"[DEBUG] Performance query for user {user_id}, week {week_number} ({week_start} to {week_end})")
        print(f"[DEBUG] Found {len(rows)} module(s) with quiz results")
        if rows:
            for row in rows:
                print(f"[DEBUG]   - {row['module_name']}: {row['avg_score']:.1f}% avg ({row['quiz_count']} quizzes)")
        else:
            # Debug: check if ANY quiz results exist for this user
            with self._get_connection() as conn:
                all_results = conn.execute(
                    '''
                    SELECT q.result_id, s.subject, s.summary_date, q.correct_count, q.question_count, q.created_at
                    FROM quiz_results q
                    JOIN daily_summary_cards s ON q.summary_id = s.summary_id
                    WHERE q.user_id = ?
                    ORDER BY q.created_at DESC
                    LIMIT 10
                    ''',
                    (user_id,)
                ).fetchall()
            print(f"[DEBUG] Total quiz results for user: {len(all_results)}")
            if all_results:
                print(f"[DEBUG] Sample quiz results:")
                for r in all_results[:3]:
                    print(f"[DEBUG]   - {r['subject']} on {r['summary_date']}: {r['correct_count']}/{r['question_count']}")

        if not rows or len(rows) == 0:
            print(f"[DEBUG] Returning empty performance highlights")
            return self._empty_performance_highlights()

        # Convert to list of dicts
        module_scores = []
        for row in rows:
            module_scores.append({
                'module_name': row['module_name'],
                'avg_score': round(row['avg_score'], 1),
                'quiz_count': row['quiz_count'],
                'total_correct': row['total_correct'],
                'total_questions': row['total_questions']
            })

        # Strongest = highest score (first in list due to ORDER BY DESC)
        strongest = module_scores[0]

        # Needs attention = lowest score (last in list)
        needs_attention = module_scores[-1]

        return {
            'strongest': {
                'module_name': strongest['module_name'],
                'score': strongest['avg_score'],
                'quiz_count': strongest['quiz_count']
            },
            'needs_attention': {
                'module_name': needs_attention['module_name'],
                'score': needs_attention['avg_score'],
                'quiz_count': needs_attention['quiz_count']
            },
            'all_modules': module_scores
        }

    def _empty_performance_highlights(self) -> Dict[str, Any]:
        """Return empty performance highlights when no quiz data available."""
        return {
            'strongest': {
                'module_name': 'No quiz data',
                'score': 0,
                'quiz_count': 0
            },
            'needs_attention': {
                'module_name': 'No quiz data',
                'score': 0,
                'quiz_count': 0
            },
            'all_modules': []
        }

    def get_upcoming_deadlines(self, user_id: int, limit: int = 3) -> List[Dict]:
        """
        Get the 3 closest upcoming deadlines from extraction_cache assessments.
        Returns a list of deadline dictionaries sorted by due date (soonest first).

        Each deadline contains:
        - title: assessment title
        - module_code: module code
        - module_name: module name
        - due_date: due date string
        - week_number: week number
        - weightage: assessment weightage
        - days_until: calculated days until deadline (from today)
        - urgency: 'urgent' (<= 5 days), 'soon' (6-14 days), or None
        """
        from datetime import datetime as _dt, timedelta as _td

        # Get extraction cache - note the nested structure
        cache_data = self.get_extraction_result_json(user_id)
        if not cache_data:
            return []

        # Access the nested extraction_result
        extraction_result = cache_data.get('extraction_result', {})
        if not extraction_result:
            return []

        assessments = extraction_result.get('assessments', [])
        if not assessments:
            return []

        # Get semester start date to calculate actual dates from week numbers
        prefs = self.get_study_preferences_by_user_id(user_id)
        semester_start = None
        if prefs and prefs.get('semester_start_date'):
            try:
                semester_start = _dt.strptime(prefs['semester_start_date'], '%Y-%m-%d')
            except Exception:
                pass

        if not semester_start:
            return []

        today = _dt.now().date()
        deadlines = []

        for assessment in assessments:
            # Skip assessments without week_number
            if not assessment.get('week_number'):
                continue

            # Skip milestone events that are not actual deadlines
            # (Release events are not deadlines, but Submission/Test/Assignment are)
            milestone_type = assessment.get('milestone_type', '')
            assessment_type = assessment.get('assessment_type', '')

            # Skip if it's only a Release (not a deadline)
            if milestone_type == 'Release' and assessment_type == 'Release':
                continue

            try:
                week_num = int(assessment['week_number'])
                # Calculate deadline as the end of that week (Sunday)
                week_start = semester_start + _td(weeks=week_num - 1)
                due_date = (week_start + _td(days=6)).date()
            except Exception:
                continue

            # Calculate days until deadline
            days_until = (due_date - today).days

            # Only include upcoming deadlines (not past ones)
            if days_until < 0:
                continue

            # Determine urgency
            urgency = None
            if days_until <= 5:
                urgency = 'urgent'
            elif days_until <= 14:
                urgency = 'soon'

            # Clean up weightage
            weightage = assessment.get('weightage', '').replace('%', '').strip()

            deadlines.append({
                'title': assessment.get('title', 'Untitled Assessment'),
                'module_code': assessment.get('module_code', ''),
                'module_name': assessment.get('module_name', ''),
                'due_date': due_date.strftime('%Y-%m-%d'),
                'week_number': str(week_num),
                'weightage': weightage,
                'days_until': days_until,
                'urgency': urgency,
            })

        # Sort by due date (soonest first) and return the 3 closest
        deadlines.sort(key=lambda x: x['days_until'])
        return deadlines[:limit]
