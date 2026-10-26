from typing import Any, Dict
from datetime import datetime, timedelta


def build_user_page_context(db, user_id: int) -> Dict[str, Any]:
    preferences = db.get_study_preferences_by_user_id(user_id)
    modules = db.get_user_modules_by_user_id(user_id)
    study_apps = db.get_study_apps_by_user_id(user_id)
    occupied_times = db.get_occupied_times_by_user_id(user_id)
    uploaded_files = db.get_uploaded_files_by_user_id(user_id)

    # Calculate current week number
    current_week = None
    if preferences and preferences.get('semester_start_date'):
        try:
            start_date = datetime.strptime(preferences.get('semester_start_date'), '%Y-%m-%d')
            today = datetime.now()
            days_diff = (today - start_date).days
            current_week = (days_diff // 7) + 1
        except Exception:
            current_week = None

    # Calculate setup percentage
    setup_checks = [
        len(modules) > 0,
        len(study_apps) > 0,
        len(uploaded_files) > 0,
        preferences is not None
    ]
    setup_percentage = int((sum(setup_checks) / len(setup_checks)) * 100)

    # Get upload counts by role
    class_upload_count = sum(1 for f in uploaded_files if f.get('file_role') == 'class_timetable')
    module_upload_count = sum(1 for f in uploaded_files if f.get('file_role') == 'module_timetable')

    # Get study app type counts
    desktop_apps_count = sum(1 for app in study_apps if app.get('app_type') == 'desktop')
    website_apps_count = sum(1 for app in study_apps if app.get('app_type') == 'website')
    both_apps_count = sum(1 for app in study_apps if app.get('app_type') == 'both')

    return {
        'preferences': preferences,
        'modules': modules,
        'study_apps': study_apps,
        'occupied_times': occupied_times,
        'uploaded_files': uploaded_files,
        'current_week': current_week,
        'setup_percentage': setup_percentage,
        'summary': {
            'modules_count': len(modules),
            'study_apps_count': len(study_apps),
            'occupied_times_count': len(occupied_times),
            'uploaded_files_count': len(uploaded_files),
            'class_upload_count': class_upload_count,
            'module_upload_count': module_upload_count,
            'desktop_apps_count': desktop_apps_count,
            'website_apps_count': website_apps_count,
            'both_apps_count': both_apps_count,
        },
    }


def build_upload_page_context(db, user_id: int) -> Dict[str, Any]:
    page_context = build_user_page_context(db, user_id)
    return {
        **page_context,
        'existing_preferences': page_context['preferences'],
        'existing_modules': page_context['modules'],
        'existing_study_apps': page_context['study_apps'],
        'recent_uploads': page_context['uploaded_files'][:5],
    }
