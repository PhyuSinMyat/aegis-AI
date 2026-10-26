import atexit
import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psutil

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from backend.routes.upload_routes import upload_bp
from backend.routes.plan_routes import plan_bp
from backend.routes.summary_routes import summary_bp
from backend.routes.tracker_routes import tracker_bp, sync_desktop_tracker_config
from backend.routes.coach_routes import coach_bp
from backend.routes.assignment_checker_routes import assignment_checker_bp
from backend.services.summary_scheduler import init_scheduler
from backend.services.planning_scheduler import init_planning_scheduler, shutdown_planning_scheduler
from backend.services.email_service import EmailService
from backend.utils.password_validator import PasswordValidator
from backend.utils.template_context import build_user_page_context
from config import Config
from database import DatabaseHelper
from form import CreateUserForm, LoginForm, ProfileForm

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / 'frontend' / 'templates'
STATIC_DIR = BASE_DIR / 'frontend' / 'static'
UPLOAD_DIR = BASE_DIR / 'uploads' / 'timetables'
PROFILE_PIC_DIR = STATIC_DIR / 'profile_pics'
ALLOWED_PROFILE_PIC_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

TRAY_SCRIPT = BASE_DIR / 'smartedu_tray.py'
_tray_process: subprocess.Popen | None = None

def _stop_existing_trays() -> None:
    current_pid = os.getpid()
    tray_processes: list[psutil.Process] = []

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == current_pid:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            if "smartedu_tray.py" in cmdline:
                tray_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for proc in tray_processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    _, alive = psutil.wait_procs(tray_processes, timeout=5)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def _launch_tray() -> None:
    global _tray_process
    if not TRAY_SCRIPT.exists():
        return
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        return

    try:
        _stop_existing_trays()
    except Exception as exc:
        print(f"[Tray] Could not stop existing tray process: {exc}")

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw) if pythonw.exists() else str(exe)

    try:
        _tray_process = subprocess.Popen(
            [runner, str(TRAY_SCRIPT), "--parent-pid", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as exc:
        _tray_process = None
        print(f"[Tray] Could not launch Aegis tray tracker: {exc}")

def _stop_launched_tray() -> None:
    global _tray_process
    proc = _tray_process
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass
    finally:
        _tray_process = None

atexit.register(_stop_launched_tray)


def create_app():
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
    app.secret_key = Config.SECRET_KEY
    app.permanent_session_lifetime = timedelta(days=7)
    app.config['SESSION_COOKIE_SECURE']   = Config.SESSION_COOKIE_SECURE
    app.config['SESSION_COOKIE_SAMESITE'] = Config.SESSION_COOKIE_SAMESITE
    app.config['SESSION_COOKIE_HTTPONLY'] = Config.SESSION_COOKIE_HTTPONLY

    app.config['UPLOAD_FOLDER'] = str(UPLOAD_DIR)
    app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PIC_DIR.mkdir(parents=True, exist_ok=True)

    @app.after_request
    def allow_extension_origin(response):
        origin = request.headers.get('Origin', '')
        if origin.startswith('chrome-extension://') or origin.startswith('moz-extension://'):
            response.headers['Access-Control-Allow-Origin']      = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers']     = 'Content-Type'
            response.headers['Access-Control-Allow-Methods']     = 'GET, POST, OPTIONS'
        return response

    @app.route('/tracker/heartbeat', methods=['OPTIONS'])
    @app.route('/tracker/status', methods=['OPTIONS'])
    def handle_preflight():
        return '', 204

    def wants_json_response() -> bool:
        return (
            request.path.startswith('/api/')
            or request.accept_mimetypes.best == 'application/json'
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )

    def render_error_response(status_code: int, title: str, message: str):
        if wants_json_response():
            return jsonify({'error': message}), status_code

        return render_template(
            'error.html',
            status_code=status_code,
            title=title,
            message=message,
            action_label='Back to dashboard',
            action_url=url_for('upload.upload') if session.get('user_id') else url_for('login'),
            current_page='',
            hide_sidebar=True,
            hide_navbar=True,
        ), status_code

    @app.errorhandler(404)
    def not_found_error(error):
        return render_error_response(
            404,
            'Page not found',
            'We could not find the page you were looking for.'
        )

    @app.errorhandler(413)
    def file_too_large_error(error):
        return render_error_response(
            413,
            'File too large',
            'That file is too large to upload. Please choose a smaller file and try again.'
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        app.logger.exception('Unhandled server error: %s', error)
        return render_error_response(
            500,
            'Oops, something went wrong',
            'Something went wrong on our side. Please try again in a moment.'
        )

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        if isinstance(error, HTTPException):
            return render_error_response(
                error.code or 500,
                error.name,
                error.description
            )

        app.logger.exception('Unhandled exception')
        return render_error_response(
            500,
            'Oops, something went wrong',
            'Something went wrong on our side. Please try again in a moment.'
        )

    db = DatabaseHelper()
    app.register_blueprint(upload_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(summary_bp)
    app.register_blueprint(tracker_bp)
    app.register_blueprint(coach_bp)
    app.register_blueprint(assignment_checker_bp)


    @app.context_processor
    def inject_current_user():
        user_id = session.get('user_id')
        current_user = db.get_user_by_id(user_id) if user_id else None
        if current_user and not session.get('username'):
            session['username'] = current_user.get_username()
        avatar_url = None
        if current_user and current_user.get_profile_pic():
            avatar_url = url_for('static', filename=current_user.get_profile_pic())
        return {
            'current_user': current_user,
            'current_user_initials': (
                (current_user.get_username()[:2] if current_user else 'U').upper()
            ),
            'current_user_avatar_url': avatar_url,
        }
    
    # Initialize the background schedulers.
    # In debug mode the Werkzeug reloader spawns a child process (WERKZEUG_RUN_MAIN='true').
    # Start schedulers only in that child to avoid double-init.
    # In production (FLASK_DEBUG=0) there is no reloader, so always start.
    if Config.FLASK_DEBUG and not os.environ.get('WERKZEUG_RUN_MAIN'):
        print("[AppInit] Debug reloader parent – schedulers will start in child process")
    else:
        print("[AppInit] Starting schedulers")
        try:
            # Initialize daily summary scheduler (existing)
            init_scheduler(app, db)
            print("[AppInit] OK Summary scheduler successfully initialized")

            # Initialize weekly planning scheduler (new)
            init_planning_scheduler(app)
            print("[AppInit] OK Planning scheduler successfully initialized")
        except Exception as exc:
            print(f"[AppInit] FAIL Failed to initialize schedulers: {exc}")
            raise

        # Register shutdown handler for planning scheduler
        atexit.register(shutdown_planning_scheduler)

    def require_login():
        user_id = session.get('user_id')
        if user_id is None:
            flash('Please log in first.', 'danger')
            return None
        return user_id

    @app.route('/')
    def home():
        return redirect(url_for('welcome'))

    @app.route('/welcome')
    def welcome():
        return render_template('welcome.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        form = LoginForm(request.form)
        if request.method == 'POST' and form.validate():
            user = db.get_user_by_identifier((form.identifier.data or '').strip().lower())
            if not user or not check_password_hash(user.get_password_hash(), form.password.data):
                flash('Incorrect login details.', 'danger')
                return render_template('login.html', form=form)
            session['user_id'] = user.get_user_id()
            session['username'] = user.get_username()
            session.permanent = bool(form.remember_me.data)
            sync_desktop_tracker_config(user.get_user_id(), username=user.get_username())
            flash('Logged in successfully!', 'success')
            return redirect(url_for('reflection'))
        return render_template('login.html', form=form)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        form = CreateUserForm(request.form)
        if request.method == 'POST' and form.validate():
            username = (form.username.data or '').strip().lower()
            email = (form.email.data or '').strip().lower()
            if db.get_user_by_username(username):
                form.username.errors.append('Username already taken.')
                return render_template('register.html', form=form)
            if db.get_user_by_identifier(email):
                form.email.errors.append('Email already registered.')
                return render_template('register.html', form=form)

            profile_pic_rel_path = ''
            saved_profile_pic_path = None
            profile_pic_file = request.files.get('profile_pic')
            if profile_pic_file and profile_pic_file.filename:
                file_extension = Path(profile_pic_file.filename).suffix.lower().lstrip('.')
                if file_extension not in ALLOWED_PROFILE_PIC_EXTENSIONS:
                    flash('Profile picture must be a PNG, JPG, JPEG, GIF, or WEBP image.', 'danger')
                    return render_template('register.html', form=form)

                safe_name = secure_filename(profile_pic_file.filename)
                unique_name = f"user_new_{uuid4().hex[:8]}_{safe_name}"
                save_path = PROFILE_PIC_DIR / unique_name
                profile_pic_file.save(save_path)
                saved_profile_pic_path = save_path
                profile_pic_rel_path = f"profile_pics/{unique_name}"

            try:
                user_id = db.insert_user(
                    username,
                    email,
                    generate_password_hash(form.password.data),
                    profile_pic=profile_pic_rel_path,
                )
            except sqlite3.IntegrityError:
                if saved_profile_pic_path and saved_profile_pic_path.exists():
                    try:
                        saved_profile_pic_path.unlink()
                    except OSError:
                        app.logger.warning("Could not remove profile picture after registration failure: %s", saved_profile_pic_path)
                form.username.errors.append('Username or email already registered.')
                return render_template('register.html', form=form)
            session['user_id'] = user_id
            session['username'] = username
            session.permanent = True
            sync_desktop_tracker_config(user_id, username=username)
            flash('Account created successfully!', 'success')
            return redirect(url_for('register_profile'))
        return render_template('register.html', form=form)

    @app.route('/register/profile', methods=['GET', 'POST'])
    def register_profile():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('register'))

        form = ProfileForm(request.form)
        if request.method == 'POST' and form.validate():
            current_user = db.get_user_by_id(user_id)
            if current_user is None:
                flash('Account not found.', 'danger')
                return redirect(url_for('register'))

            first_name = (form.first_name.data or '').strip()
            last_name = (form.last_name.data or '').strip()
            date_of_birth = (form.date_of_birth.data or '').strip()
            phone_number = (form.phone_number.data or '').strip()

            profile_pic_rel_path = current_user.get_profile_pic()
            profile_pic_file = request.files.get('profile_pic')
            if profile_pic_file and profile_pic_file.filename:
                file_extension = Path(profile_pic_file.filename).suffix.lower().lstrip('.')
                if file_extension not in ALLOWED_PROFILE_PIC_EXTENSIONS:
                    flash('Profile picture must be a PNG, JPG, JPEG, GIF, or WEBP image.', 'danger')
                    return render_template('register_profile.html', form=form)

                safe_name = secure_filename(profile_pic_file.filename)
                unique_name = f"user_{user_id}_{uuid4().hex[:8]}_{safe_name}"
                save_path = PROFILE_PIC_DIR / unique_name
                profile_pic_file.save(save_path)
                profile_pic_rel_path = f"profile_pics/{unique_name}"

            db.update_user_profile(
                user_id,
                current_user.get_username(),
                profile_pic_rel_path,
                first_name=first_name,
                last_name=last_name,
                date_of_birth=date_of_birth,
                phone_number=phone_number
            )

            flash('Profile completed. Please upload your timetable files.', 'success')
            return redirect(url_for('upload.upload'))

        return render_template('register_profile.html', form=form)

    @app.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            if not email:
                return render_template('forgot_password.html', error='Please enter your email address.')

            user = db.get_user_by_email(email)
            if not user:
                return render_template('forgot_password.html', error='No account found with that email address.')

            db.cleanup_expired_reset_tokens()
            db.delete_password_reset_token(email)

            otp_code = EmailService.generate_otp()
            expires_at = EmailService.calculate_expiry_time(Config.OTP_EXPIRY_MINUTES)

            try:
                email_sent = EmailService.send_otp_email(email, otp_code)
            except Exception as exc:
                app.logger.error(f"Failed to send OTP email to {email}: {exc}")
                return render_template('forgot_password.html', error='Failed to send verification code. Please try again later.')

            if not email_sent:
                app.logger.error(f"Failed to send OTP email to {email}: email service returned False")
                return render_template('forgot_password.html', error='Failed to send verification code. Please check email configuration and try again.')

            db.create_password_reset_token(email, otp_code, expires_at)
            session['reset_email'] = email
            flash('Verification code sent to your email.', 'success')
            return redirect(url_for('verify_otp'))

        return render_template('forgot_password.html')

    @app.route('/verify-otp', methods=['GET', 'POST'])
    def verify_otp():
        email = session.get('reset_email')
        if not email:
            flash('Please start the password reset process.', 'danger')
            return redirect(url_for('forgot_password'))

        if request.method == 'POST':
            otp_code = (request.form.get('otp_code') or '').strip()
            if not otp_code or len(otp_code) != 6:
                return render_template('verify_otp.html', email=email, error='Please enter a valid 6-digit code.', expiry_seconds=600)

            token_record = db.get_password_reset_token(email)
            if not token_record:
                flash('Verification code expired. Please request a new one.', 'danger')
                return redirect(url_for('forgot_password'))

            if token_record.get('used'):
                flash('This code has already been used.', 'danger')
                return redirect(url_for('forgot_password'))

            if token_record.get('attempts', 0) >= Config.OTP_MAX_ATTEMPTS:
                db.delete_password_reset_token(email)
                flash('Too many incorrect attempts. Please request a new code.', 'danger')
                return redirect(url_for('forgot_password'))

            if EmailService.is_expired(token_record['expires_at']):
                db.delete_password_reset_token(email)
                flash('Verification code expired. Please request a new one.', 'danger')
                return redirect(url_for('forgot_password'))

            if token_record['otp_code'] != otp_code:
                attempts = db.increment_otp_attempts(email)
                remaining = Config.OTP_MAX_ATTEMPTS - attempts
                return render_template(
                    'verify_otp.html',
                    email=email,
                    error=f'Incorrect code. {remaining} attempts remaining.',
                    expiry_seconds=600
                )

            db.mark_token_as_used(email)
            session['otp_verified'] = True
            return redirect(url_for('reset_password'))

        from datetime import datetime
        token_record = db.get_password_reset_token(email)
        expiry_seconds = 600
        if token_record and token_record.get('expires_at'):
            try:
                expires_dt = datetime.fromisoformat(token_record['expires_at'])
                now_dt = datetime.now()
                expiry_seconds = max(0, int((expires_dt - now_dt).total_seconds()))
            except:
                expiry_seconds = 600

        return render_template('verify_otp.html', email=email, expiry_seconds=expiry_seconds)

    @app.route('/reset-password', methods=['GET', 'POST'])
    def reset_password():
        email = session.get('reset_email')
        otp_verified = session.get('otp_verified')

        if not email or not otp_verified:
            flash('Please complete the verification process first.', 'danger')
            return redirect(url_for('forgot_password'))

        if request.method == 'POST':
            password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')

            if not password or not confirm_password:
                return render_template('reset_password.html', email=email, error='Both password fields are required.')

            if password != confirm_password:
                return render_template('reset_password.html', email=email, error='Passwords do not match.')

            is_valid, errors = PasswordValidator.validate(password, strict=True)
            if not is_valid:
                return render_template('reset_password.html', email=email, error=' '.join(errors))

            password_hash = generate_password_hash(password)
            success = db.update_user_password(email, password_hash)

            if not success:
                return render_template('reset_password.html', email=email, error='Failed to update password. Please try again.')

            db.delete_password_reset_token(email)
            session.pop('reset_email', None)
            session.pop('otp_verified', None)

            flash('Password reset successfully. Please log in with your new password.', 'success')
            return redirect(url_for('login'))

        return render_template('reset_password.html', email=email)

    @app.route('/api/check-username', methods=['POST'])
    def check_username():
        data = request.get_json()
        username = (data.get('username') or '').strip().lower()
        if not username or len(username) < 3:
            return jsonify({'available': False, 'error': 'Username too short'})
        existing_user = db.get_user_by_username(username)
        return jsonify({'available': existing_user is None})

    @app.route('/api/check-email', methods=['POST'])
    def check_email():
        data = request.get_json()
        email = (data.get('email') or '').strip().lower()
        if not email or '@' not in email:
            return jsonify({'available': False, 'error': 'Invalid email'})
        existing_user = db.get_user_by_email(email)
        return jsonify({'available': existing_user is None})

    @app.route('/reflection')
    def reflection():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))

        # Fetch only the weeks that have saved study plans in the database
        from backend.routes.plan_routes import _build_latest_saved_plan_history
        from backend.agents.planning_agent import compute_academic_week
        raw_plans = db.get_study_plans_by_user_id(user_id) or []
        history = _build_latest_saved_plan_history(raw_plans)
        available_weeks = sorted(
            [item['week_number'] for item in history if item.get('week_number') is not None]
        )

        # Determine which week to show: prefer current week if it exists in available_weeks, else last week
        display_week = None
        if available_weeks:
            # Get user's semester start date to calculate current week
            preferences = db.get_study_preferences_by_user_id(user_id)
            if preferences and preferences.get('semester_start_date'):
                from datetime import date
                current_week_number = compute_academic_week(preferences['semester_start_date'], date.today().isoformat())
                # If current week has a saved plan, use it; otherwise fall back to last available week
                if current_week_number in available_weeks:
                    display_week = current_week_number
                else:
                    display_week = available_weeks[-1]
            else:
                # No semester start date, default to last available week
                display_week = available_weeks[-1]

        # Pre-compute real stats for the display week so the page renders with live data
        initial_stats = None
        initial_daily_hours = []
        initial_module_stats = []
        initial_study_patterns = None
        initial_performance_highlights = None
        if display_week is not None:
            initial_stats = db.get_reflection_week_stats(user_id, display_week)
            initial_daily_hours = db.get_daily_hours_for_week(user_id, display_week)
            initial_module_stats = db.get_reflection_module_stats(user_id, display_week)
            initial_study_patterns = db.get_reflection_study_patterns(user_id, display_week)
            initial_performance_highlights = db.get_reflection_performance_highlights(user_id, display_week)

            # Debug: print what we got
            print(f"[DEBUG] Performance highlights for week {display_week}: {initial_performance_highlights}")

        # Get upcoming deadlines from extraction cache
        upcoming_deadlines = db.get_upcoming_deadlines(user_id, limit=3)

        return render_template(
            'reflection.html',
            current_page='reflection',
            available_weeks=available_weeks,
            display_week=display_week,
            initial_stats=initial_stats,
            initial_daily_hours=initial_daily_hours,
            initial_module_stats=initial_module_stats,
            initial_study_patterns=initial_study_patterns,
            initial_performance_highlights=initial_performance_highlights,
            upcoming_deadlines=upcoming_deadlines,
            **build_user_page_context(db, user_id)
        )

    @app.route('/api/reflection/available-weeks')
    def get_reflection_available_weeks():
        user_id = require_login()
        if user_id is None:
            return jsonify({'error': 'Authentication required'}), 401

        from backend.routes.plan_routes import _build_latest_saved_plan_history
        raw_plans = db.get_study_plans_by_user_id(user_id) or []
        history = _build_latest_saved_plan_history(raw_plans)
        available_weeks = sorted(
            [item['week_number'] for item in history if item.get('week_number') is not None]
        )
        return jsonify({'available_weeks': available_weeks})

    @app.route('/api/reflection/week/<int:week_number>')
    def get_reflection_week_data(week_number):
        user_id = require_login()
        if user_id is None:
            return jsonify({'error': 'Authentication required'}), 401

        # Validate that this week actually exists in the saved study plans
        from backend.routes.plan_routes import _build_latest_saved_plan_history
        raw_plans = db.get_study_plans_by_user_id(user_id) or []
        history = _build_latest_saved_plan_history(raw_plans)
        available_weeks = [item['week_number'] for item in history if item.get('week_number') is not None]

        if week_number not in available_weeks:
            return jsonify({'error': f'Week {week_number} has no saved study plan'}), 404

        # Fetch real stats from the database
        stats = db.get_reflection_week_stats(user_id, week_number)
        daily_hours = db.get_daily_hours_for_week(user_id, week_number)
        module_stats = db.get_reflection_module_stats(user_id, week_number)
        study_patterns = db.get_reflection_study_patterns(user_id, week_number)
        performance_highlights = db.get_reflection_performance_highlights(user_id, week_number)
        return jsonify({
            'week_number': week_number,
            'stats': stats,
            'daily_hours': daily_hours,
            'module_stats': module_stats,
            'study_patterns': study_patterns,
            'performance_highlights': performance_highlights,
        })

    @app.route('/api/debug/quiz-results')
    def debug_quiz_results():
        """Debug endpoint to check if quiz results are being saved"""
        if not Config.FLASK_DEBUG:
            return jsonify({'error': 'Not found'}), 404

        user_id = require_login()
        if user_id is None:
            return jsonify({'error': 'Authentication required'}), 401

        # Get all quiz results for this user
        with db._get_connection() as conn:
            results = conn.execute(
                '''
                SELECT q.*, s.subject, s.summary_date, s.topic
                FROM quiz_results q
                JOIN daily_summary_cards s ON q.summary_id = s.summary_id
                WHERE q.user_id = ?
                ORDER BY q.created_at DESC
                LIMIT 20
                ''',
                (user_id,)
            ).fetchall()

        return jsonify({
            'count': len(results),
            'results': [dict(r) for r in results]
        })

    @app.route('/profile', methods=['GET', 'POST'])
    def profile():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        if request.method == 'POST':
            current_user = db.get_user_by_id(user_id)
            if current_user is None:
                flash('Could not load your account details.', 'danger')
                return redirect(url_for('profile'))

            new_username = (request.form.get('username') or '').strip().lower()
            if not new_username:
                flash('Username cannot be empty.', 'danger')
                return redirect(url_for('profile'))
            if len(new_username) < 3:
                flash('Username must be at least 3 characters long.', 'danger')
                return redirect(url_for('profile'))

            existing_user = db.get_user_by_username(new_username)
            if existing_user and existing_user.get_user_id() != user_id:
                flash('That username is already taken.', 'danger')
                return redirect(url_for('profile'))

            profile_pic_rel_path = current_user.get_profile_pic()
            profile_pic_file = request.files.get('profile_pic')
            if profile_pic_file and profile_pic_file.filename:
                file_extension = Path(profile_pic_file.filename).suffix.lower().lstrip('.')
                if file_extension not in ALLOWED_PROFILE_PIC_EXTENSIONS:
                    flash('Profile picture must be a PNG, JPG, JPEG, GIF, or WEBP image.', 'danger')
                    return redirect(url_for('profile'))

                safe_name = secure_filename(profile_pic_file.filename)
                unique_name = f"user_{user_id}_{uuid4().hex[:8]}_{safe_name}"
                save_path = PROFILE_PIC_DIR / unique_name
                profile_pic_file.save(save_path)
                profile_pic_rel_path = f"profile_pics/{unique_name}"

                old_profile_pic = current_user.get_profile_pic()
                if old_profile_pic:
                    old_path = STATIC_DIR / old_profile_pic
                    if old_path.exists() and old_path.is_file():
                        try:
                            old_path.unlink()
                        except OSError:
                            app.logger.warning("Could not remove previous profile picture: %s", old_path)

            first_name = (request.form.get('first_name') or '').strip()
            last_name = (request.form.get('last_name') or '').strip()
            date_of_birth = (request.form.get('date_of_birth') or '').strip()
            phone_number = (request.form.get('phone_number') or '').strip()

            db.update_user_profile(
                user_id, new_username, profile_pic_rel_path,
                first_name=first_name,
                last_name=last_name,
                date_of_birth=date_of_birth,
                phone_number=phone_number
            )
            session['username'] = new_username
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('profile'))

        return render_template('profile.html', current_page='profile', hide_sidebar=True, **build_user_page_context(db, user_id))

    @app.route('/settings')
    def settings():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        return render_template('settings.html', current_page='settings', **build_user_page_context(db, user_id))

    @app.route('/settings/profile')
    def settings_profile():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        return render_template('settings_profile.html', current_page='settings', **build_user_page_context(db, user_id))

    @app.route('/settings/account')
    def settings_account():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        return render_template('settings_account.html', current_page='settings', **build_user_page_context(db, user_id))

    @app.route('/settings/appearance')
    def settings_appearance():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        return render_template('settings_appearance.html', current_page='settings', **build_user_page_context(db, user_id))

    @app.route('/settings/notifications')
    def settings_notifications():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))
        return render_template('settings_notifications.html', current_page='settings', **build_user_page_context(db, user_id))

    @app.route('/delete-account', methods=['POST'])
    def delete_account():
        user_id = require_login()
        if user_id is None:
            return redirect(url_for('login'))

        current_user = db.get_user_by_id(user_id)
        if current_user is None:
            session.clear()
            flash('Account not found. Please log in again.', 'warning')
            return redirect(url_for('login'))

        # Delete uploaded timetable files from disk.
        for item in db.get_uploaded_files_by_user_id(user_id):
            raw_path = (item.get('file_path') or '').strip()
            if not raw_path:
                continue
            try:
                file_path = Path(raw_path)
                if file_path.exists() and file_path.is_file():
                    file_path.unlink()
            except OSError:
                app.logger.warning('Could not delete uploaded file during account deletion: %s', raw_path)

        # Remove per-user upload directory if empty.
        try:
            user_upload_dir = UPLOAD_DIR / f'user_{user_id}'
            if user_upload_dir.exists() and user_upload_dir.is_dir():
                for leftover in user_upload_dir.iterdir():
                    if leftover.is_file():
                        leftover.unlink()
                user_upload_dir.rmdir()
        except OSError:
            app.logger.warning('Could not fully remove upload directory for user %s', user_id)

        # Delete profile picture file from disk.
        profile_pic_rel_path = current_user.get_profile_pic()
        if profile_pic_rel_path:
            try:
                profile_pic_path = STATIC_DIR / profile_pic_rel_path
                if profile_pic_path.exists() and profile_pic_path.is_file():
                    profile_pic_path.unlink()
            except OSError:
                app.logger.warning('Could not delete profile picture during account deletion: %s', profile_pic_rel_path)

        db.delete_user_and_related_data(user_id)
        session.clear()
        flash('Your account and all related data were deleted successfully.', 'success')
        return redirect(url_for('login'))

    @app.route('/logout')
    def logout():
        session.clear()
        flash('You have been logged out.', 'success')
        return redirect(url_for('login'))

    return app


app = create_app()

if __name__ == '__main__':
    _launch_tray()
    app.run(debug=Config.FLASK_DEBUG)
