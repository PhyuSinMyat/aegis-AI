"""Focused regression checks for final tracker notifications (temporary DB only)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from backend.agents.tracker_agent import TrackerAgent
from database import DatabaseHelper


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class TestDatabase(DatabaseHelper):
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn


class TrackerResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = TestDatabase(Path(self.temp.name) / 'tracker.db')
        with patch('database.DatabaseHelper', return_value=self.db):
            self.routes = importlib.import_module('backend.routes.tracker_routes')
        self.db_patch = patch.object(self.routes, 'db', self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        app = Flask(__name__)
        app.secret_key = 'test-only'
        app.register_blueprint(self.routes.tracker_bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1

    def make_session(self, study_seconds, ended=True):
        data = {
            'user_id': 1, 'module_name': 'Test Module',
            'planned_duration_mins': 45, 'status': 'active',
            'study_seconds': study_seconds, 'inactivity_seconds': 0,
            'distraction_seconds': 0, 'current_app': '',
            'actual_start': (datetime.now() - timedelta(minutes=45 if ended else 32)).isoformat(),
            'actual_end': None, 'last_heartbeat': datetime.now().isoformat(),
        }
        if ended:
            data = TrackerAgent.end_session(data)
        data['session_id'] = self.db.insert_study_session(data)
        return data

    def claim(self, session_id):
        return self.client.post(f'/tracker/session/{session_id}/result-notification')

    def test_cutoff_and_single_final_notification(self):
        for seconds, expected in [(1889, 'Study Session Missed'), (1890, 'Congratulations!'), (1891, 'Congratulations!')]:
            with self.subTest(seconds=seconds):
                data = self.make_session(seconds)
                result = self.claim(data['session_id']).get_json()
                self.assertTrue(result['notify'])
                self.assertEqual(result['title'], expected)
                self.assertFalse(self.claim(data['session_id']).get_json()['notify'])

    def test_reaching_seventy_percent_does_not_notify_before_end(self):
        data = self.make_session(1890, ended=False)
        self.assertFalse(self.claim(data['session_id']).get_json()['notify'])
        self.assertEqual(TrackerAgent._resolve_status(data)['status'], 'active')

    def test_concurrent_desktop_and_website_claims(self):
        data = self.make_session(1890)
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(
                lambda _: self.db.claim_session_result_notification(data['session_id'], 1), range(8)
            ))
        self.assertEqual(sum(claims), 1)

    def test_watched_session_remains_available_after_another_client_ends_it(self):
        data = self.make_session(1889)
        status = self.client.get(f"/tracker/status?session_id={data['session_id']}").get_json()
        self.assertFalse(status['active'])
        self.assertEqual(status['display']['status'], 'incompleted')

    def test_auth_and_ownership(self):
        data = self.make_session(1890)
        with self.client.session_transaction() as session:
            session['user_id'] = 2
        self.assertEqual(self.claim(data['session_id']).status_code, 404)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.claim(data['session_id']).status_code, 401)

    def test_heartbeat_delivers_final_result_for_desktop_and_extension(self):
        for source in ('desktop', 'extension'):
            data = self.make_session(1890, ended=False)
            data['actual_start'] = (datetime.now() - timedelta(minutes=45)).isoformat()
            self.db.update_session(data)
            with patch.object(self.routes, 'sync_desktop_tracker_config'):
                response = self.client.post('/tracker/heartbeat', json={
                    'session_id': data['session_id'], 'elapsed_secs': 0, 'source': source,
                })
            self.assertEqual(response.get_json()['display']['status'], 'completed')
            self.assertEqual(self.claim(data['session_id']).get_json()['title'], 'Congratulations!')

    def test_fifteen_minute_streak_only_keeps_existing_milestones(self):
        import monitor
        for mode in ('inactive', 'distract'):
            with patch.object(monitor, 'show_os_notification') as notify:
                streak = monitor.StreakState()
                for _ in range(30):
                    streak.update(mode, 30, 'game.exe')
                self.assertEqual(notify.call_count, 3)
                self.assertNotIn('Study Session Missed', [c.args[0] for c in notify.call_args_list])

    def test_desktop_notification_respects_shared_claim(self):
        import smartedu_tray
        for notify in (True, False):
            with patch.object(smartedu_tray.requests, 'post') as post, \
                    patch.object(smartedu_tray, 'show_notification') as show:
                post.return_value.ok = True
                post.return_value.json.return_value = {
                    'notify': notify, 'title': 'Congratulations!', 'message': 'final',
                }
                smartedu_tray.notify_session_result('http://localhost:5000', 'test-token', 12)
                self.assertEqual(show.call_count, int(notify))

    def test_desktop_poll_keeps_ended_session_result(self):
        import smartedu_tray
        data = {'active': False, 'session_id': 12, 'display': {'status': 'incompleted'}}
        with patch.object(smartedu_tray.requests, 'get') as get:
            get.return_value.ok = True
            get.return_value.json.return_value = data
            self.assertEqual(smartedu_tray.get_active_session('http://localhost:5000', 'test-token', 12), data)
            self.assertEqual(get.call_args.kwargs['params']['session_id'], 12)


if __name__ == '__main__':
    unittest.main()
