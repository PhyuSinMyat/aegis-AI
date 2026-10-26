# Aegis AI
### An AI-powered study platform that helps students plan their schedule, understand lessons, track focus, and improve


Aegis is an AI-assisted study companion that turns class and module timetables into a personalized study plan. It combines planning, session tracking, daily learning materials, reflection, and assignment feedback in one Flask application.

## Key features

- Account registration, profile management, login, and OTP-based password recovery
- PDF timetable extraction
- AI-generated two-week study plans based on availability and study preferences
- Drag-and-drop plan adjustments and automatic replanning of missed sessions
- Scheduled study-session tracking with desktop and browser activity support
- Daily summaries, notes, flashcards, quizzes, and learning resources
- Weekly progress and reflection views
- AI study coach and assignment checker
- Optional Chrome extension for browser activity tracking

## Technology stack

- **Backend:** Python, Flask, APScheduler, Pydantic
- **Database:** SQLite
- **AI service:** Amazon Bedrock through Boto3, with a local mock mode
- **File processing:** pdfplumber and openpyxl
- **Frontend:** Jinja templates, HTML, CSS, and JavaScript
- **Desktop integration:** pystray, psutil, Plyer, and Pillow

## Project structure

```text
backend/
  agents/                 AI extraction, planning, coaching, and checking logic
  prompts/                Model prompt templates
  routes/                 Flask feature routes
  services/               Scheduling, parsing, email, and pipeline services
  schemas/                Structured extraction and planning models
frontend/
  static/                 Stylesheets, JavaScript, and generated local assets
  templates/              Jinja page templates
smartedu-extension/       Optional Chrome activity-tracking extension
app.py                    Flask application entry point
config.py                 Environment-based configuration
database.py               SQLite schema and data-access helper
requirements.txt          Python dependencies
```

## Prerequisites

- Python 3.10 or newer
- `pip`
- An AWS account with Amazon Bedrock access if real AI generation is required
- Windows for the complete desktop tray and application-monitoring experience

Mock mode can be used without AWS credentials for local setup and interface testing.

## Installation

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

### macOS or Linux

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open <http://127.0.0.1:5000> after the server starts. The SQLite database and upload directories are created locally when needed.

## Configuration

Copy `.env.example` to `.env` before starting the application. The `.env` file is ignored by Git and must never be committed.

### Mock mode

The supplied example configuration uses mock mode:

```dotenv
USE_MOCK_LLM=1
```

This is the safest option for initial setup because it does not make Amazon Bedrock requests.

### Amazon Bedrock mode

To enable real AI generation, update `.env` with credentials belonging to your own AWS account:

```dotenv
USE_MOCK_LLM=0
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=ap-southeast-1
AWS_BEDROCK_MODEL_ID=your_enabled_model_id
```

The selected model must already be enabled for the configured AWS account and region. Do not commit real credentials.

### Password-recovery email

OTP password recovery requires SMTP settings. For Gmail, use an app password rather than the account password.

```dotenv
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=1
MAIL_USE_SSL=0
MAIL_USERNAME=your_email@example.com
MAIL_PASSWORD=your_app_password
MAIL_DEFAULT_SENDER=your_email@example.com
```

## Typical workflow

1. Register an account and complete the study profile.
2. Upload one class timetable and one or more module files in PDF, CSV, or XLSX format.
3. Review the structured data extracted from the files.
4. Generate a personalized study plan for the current and following week.
5. Follow scheduled sessions in the Study Tracker; missed sessions can be replanned.
6. Use Daily Summary for notes, flashcards, quizzes, and resources.
7. Review weekly progress in Reflection.

## Optional Chrome extension

The extension reports browser activity to the local tracker during study sessions.

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Choose the `smartedu-extension` directory.
5. Keep Aegis running at `http://localhost:5000` while using the extension.

## Verification and tests

Run the authentication integration smoke test:

```powershell
python test_auth_integration.py
```

Additional diagnostic and manual test scripts are included for extraction caching, plan generation, scheduling, and replanning:

```text
test_extraction_cache.py
test_planning_scheduler.py
test_scheduler.py
test_replanning_db.py
test_replanning_comprehensive.py
```

Some scripts require an initialized local database, sample user data, or Bedrock configuration and may prompt for a user ID. Run them in a disposable development environment because scheduler and replanning tests can update local records.

## Data and security notes

- Passwords are stored as hashes rather than plaintext.
- `.env`, SQLite databases, uploaded timetables, logs, caches, and profile pictures are excluded through `.gitignore`.
- Uploaded academic files and generated study data remain in the local application directories and database.
- Mock mode is recommended for demonstrations that do not require live AI output.
- Set a strong, unique `SECRET_KEY` and disable debug logging outside local development.

## Current limitations

- SQLite and local file storage are intended for a local or small-scale demonstration rather than multi-instance production deployment.
- Desktop tray notifications and application monitoring are designed primarily for Windows.
- Live AI output depends on Amazon Bedrock availability, permissions, model access, and usage costs.
- Browser tracking requires the optional Chrome extension and a running local Aegis server.
