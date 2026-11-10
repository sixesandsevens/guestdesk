"""Regression tests for generated display slides using temporary data only."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from guestdesk import display
from guestdesk.models import Service, ServiceOverride, ServiceSeries


class BuiltinDisplayTests(unittest.TestCase):
    def setUp(self):
        from guestdesk import app as app_module, grievances

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patches = [
            patch.object(app_module, 'DATA_DIR', str(root)),
            patch.object(grievances, 'DATA_DIR', str(root)),
            patch.object(display, 'DATA_ROOT', root / 'display'),
            patch.object(display, 'DATA_PATH', root / 'display' / 'display_config.json'),
            patch.object(display, 'SLIDES_DIR', root / 'display' / 'slides'),
            patch.object(display, 'LEGACY_DATA_PATH', root / 'missing-legacy.json'),
            patch.object(app_module, 'queue_mail', lambda **kwargs: None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = app_module.create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                               BABEL_DEFAULT_TIMEZONE='America/New_York')
        self.client = self.app.test_client()
        self.config = {
            'displays': {'lobby': {'name': 'Lobby', 'active': True,
                                   'assigned_slideshow': 'main'}},
            'slideshows': {'main': {'name': 'Main', 'fade_duration': 1.4, 'slides': []}},
        }
        self.save()

    def save(self):
        display.save_config(self.config)

    def admin(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

    def settings(self, **values):
        self.admin()
        form = {'action': 'edit_builtins', 'clock_duration': '15',
                'schedule_duration': '15'}
        form.update(values)
        return self.client.post('/admin/slideshows/main', data=form)

    def add_series(self, title, start, end, rrule=None, service=None):
        with self.app.app_context():
            db = self.app.dbs()
            row = ServiceSeries(title=title, dtstart=start, dtend=end,
                                rrule=rrule, is_active=True, service=service)
            db.add(row)
            db.commit()
            return row.id

    def fixed_now(self, year=2026, month=6, day=3, hour=12):
        tz = ZoneInfo('America/New_York')
        return patch.object(display, '_local_now',
                            return_value=(datetime(year, month, day, hour, tzinfo=tz), tz.key, tz))

    def test_old_configuration_defaults_off_and_manual_api_unchanged(self):
        self.config['slideshows']['main']['slides'] = [
            {'id': 4, 'type': 'text', 'headline': 'Welcome', 'order': 1,
             'duration': 12, 'active': True}]
        self.save()
        data = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual([s['type'] for s in data['slides']], ['text'])
        self.assertEqual(data['slides'][0]['id'], 4)
        self.assertEqual(data['schedule_state'], 'disabled')

    def test_settings_independent_validation_and_duplication(self):
        self.settings(clock_enabled='on', clock_duration='27')
        saved = display.load_config()['slideshows']['main']
        self.assertTrue(saved['clock_enabled'])
        self.assertFalse(saved['schedule_enabled'])
        self.assertEqual(saved['clock_duration'], 27)
        self.settings(schedule_enabled='on', schedule_duration='44', clock_duration='27')
        saved = display.load_config()['slideshows']['main']
        self.assertFalse(saved['clock_enabled'])
        self.assertTrue(saved['schedule_enabled'])
        self.assertEqual(saved['schedule_duration'], 44)
        response = self.settings(schedule_enabled='on', schedule_duration='301')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(display.load_config()['slideshows']['main']['schedule_duration'], 44)
        with self.client.session_transaction() as session:
            self.assertIn('whole number from 1 to 300', str(session['_flashes']))
        for bad in ('0', '1.5', 'abc'):
            self.settings(schedule_enabled='on', schedule_duration=bad)
            self.assertEqual(display.load_config()['slideshows']['main']['schedule_duration'], 44)
        self.client.post('/admin/slideshows', data={'action': 'duplicate_slideshow', 'slug': 'main'})
        clone = display.load_config()['slideshows']['main-2']
        self.assertTrue(clone['schedule_enabled'])
        self.assertEqual(clone['schedule_duration'], 44)

    def test_clock_only_and_api_parity(self):
        self.config['slideshows']['main'].update(clock_enabled=True, clock_duration=19)
        self.save()
        display_data = self.client.get('/api/display-slides/lobby').get_json()
        preview_data = self.client.get('/api/slideshow-slides/main').get_json()
        self.assertEqual(display_data['slides'], preview_data['slides'])
        self.assertEqual(display_data['slides'], [
            {'type': 'clock', 'key': 'builtin:clock', 'duration': 19, 'transition': 'fade'}])
        self.assertIn('server_time', display_data)
        self.assertEqual(display_data['timezone'], 'America/New_York')

    def test_preview_uses_same_player_and_duration_override(self):
        self.config['slideshows']['main']['clock_enabled'] = True
        self.save()
        preview = self.client.get('/slideshows/main/preview?duration=1.5')
        display_page = self.client.get('/displays/lobby')
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b'previewDuration: 1.5', preview.data)
        self.assertIn(b'/static/js/display_player.js', preview.data)
        self.assertIn(b'/static/js/display_player.js', display_page.data)

    def test_schedule_only_empty_and_failure_are_distinct(self):
        self.config['slideshows']['main']['schedule_enabled'] = True
        self.save()
        with self.fixed_now():
            empty = self.client.get('/api/display-slides/lobby').get_json()
            self.assertEqual(empty['slides'], [])
            self.assertEqual(empty['schedule_state'], 'empty')
            with patch.object(display, 'today_services', side_effect=RuntimeError('offline')):
                failed = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual(failed['slides'], [])
        self.assertEqual(failed['schedule_state'], 'unavailable')

    def test_schedule_only_and_both_builtins_without_manual_slides(self):
        self.config['slideshows']['main']['schedule_enabled'] = True
        self.save()
        self.add_series('Lunch', datetime(2026, 6, 3, 11), datetime(2026, 6, 3, 13))
        with self.fixed_now():
            schedule_only = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual([s['type'] for s in schedule_only['slides']], ['schedule'])
        self.config['slideshows']['main']['clock_enabled'] = True
        self.save()
        with self.fixed_now():
            both = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual([s['type'] for s in both['slides']], ['clock', 'schedule'])

    def test_calendar_failure_keeps_clock_and_manual_slides(self):
        self.config['slideshows']['main'].update(clock_enabled=True, schedule_enabled=True,
            slides=[{'id': 3, 'type': 'text', 'headline': 'Hello', 'order': 1,
                     'duration': 8, 'active': True}])
        self.save()
        with self.fixed_now(), patch.object(display, 'today_services', side_effect=RuntimeError('offline')):
            data = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual(data['schedule_state'], 'unavailable')
        self.assertEqual([slide['type'] for slide in data['slides']], ['clock', 'text'])

    def test_bulk_manual_duration_leaves_builtins_alone(self):
        self.config['slideshows']['main'].update(clock_enabled=True, clock_duration=25,
            schedule_duration=32, slides=[{'id': 1, 'type': 'text', 'headline': 'A',
                                         'order': 1, 'duration': 8, 'active': True}])
        self.save()
        self.admin()
        self.client.post('/admin/slideshows/main', data={'action': 'set_duration_all', 'duration': '7'})
        saved = display.load_config()['slideshows']['main']
        self.assertEqual(saved['slides'][0]['duration'], 7)
        self.assertEqual((saved['clock_duration'], saved['schedule_duration']), (25, 32))

    def test_order_pagination_and_projection(self):
        self.config['slideshows']['main'].update(clock_enabled=True, schedule_enabled=True,
                                                 schedule_duration=21, slides=[
            {'id': 7, 'type': 'text', 'headline': 'Later', 'order': 2, 'duration': 9, 'active': True},
            {'id': 8, 'type': 'text', 'headline': 'First', 'order': 1, 'duration': 9, 'active': True},
            {'id': 9, 'type': 'text', 'headline': 'Hidden', 'order': 3, 'duration': 9, 'active': False},
        ])
        self.save()
        for hour in (8, 10, 12):
            self.add_series('X' * 100 if hour == 10 else f'Service {hour}',
                            datetime(2026, 6, 3, hour), datetime(2026, 6, 3, hour + 1))
        with self.fixed_now():
            real = self.client.get('/api/display-slides/lobby').get_json()
            preview = self.client.get('/api/slideshow-slides/main').get_json()
        self.assertEqual(real['slides'], preview['slides'])
        self.assertEqual([s['type'] for s in real['slides']],
                         ['clock', 'schedule', 'schedule', 'schedule', 'text', 'text'])
        self.assertEqual([s['id'] for s in real['slides'] if s['type'] == 'text'], [8, 7])
        self.assertTrue(all(s['duration'] == 21 for s in real['slides'] if s['type'] == 'schedule'))
        event = real['slides'][1]['events'][0]
        self.assertEqual(set(event), {'title', 'location', 'start', 'end'})

    def test_recurring_cancellation_and_rescheduling(self):
        series_id = self.add_series('Recurring', datetime(2026, 6, 1, 9),
                                    datetime(2026, 6, 1, 10), 'FREQ=DAILY;COUNT=4')
        with self.app.app_context():
            db = self.app.dbs()
            db.add(ServiceOverride(series_id=series_id, instance_start=datetime(2026, 6, 3, 9),
                                   cancelled=True))
            db.add(ServiceOverride(series_id=series_id, instance_start=datetime(2026, 6, 2, 9),
                                   new_dtstart=datetime(2026, 6, 3, 15),
                                   new_dtend=datetime(2026, 6, 3, 16), new_title='Moved'))
            db.commit()
            with self.fixed_now():
                events = display.today_services(datetime(2026, 6, 3, 12,
                    tzinfo=ZoneInfo('America/New_York')), ZoneInfo('America/New_York'))
        self.assertEqual([ev['title'] for ev in events], ['Moved'])

    def test_local_day_overnight_tomorrow_and_dst(self):
        self.add_series('Overnight', datetime(2026, 3, 7, 23), datetime(2026, 3, 8, 1))
        self.add_series('Tomorrow', datetime(2026, 3, 9, 0), datetime(2026, 3, 9, 1))
        tz = ZoneInfo('America/New_York')
        with self.app.app_context():
            events = display.today_services(datetime(2026, 3, 8, 12, tzinfo=tz), tz)
        self.assertEqual([ev['title'] for ev in events], ['Overnight'])
        self.assertEqual(events[0]['end'][-6:], '-05:00')
        self.add_series('Fall morning', datetime(2026, 11, 1, 0, 30),
                        datetime(2026, 11, 1, 1, 30))
        with self.app.app_context():
            fall = display.today_services(datetime(2026, 11, 1, 12, tzinfo=tz), tz)
        self.assertEqual([ev['title'] for ev in fall], ['Fall morning'])

    def test_rescheduled_overnight_from_outside_query_window(self):
        series_id = self.add_series('Original', datetime(2026, 5, 1, 9),
                                    datetime(2026, 5, 1, 10))
        with self.app.app_context():
            db = self.app.dbs()
            db.add(ServiceOverride(series_id=series_id,
                instance_start=datetime(2026, 5, 1, 9),
                new_dtstart=datetime(2026, 6, 2, 23),
                new_dtend=datetime(2026, 6, 3, 1), new_title='Moved overnight'))
            db.commit()
            tz = ZoneInfo('America/New_York')
            events = display.today_services(datetime(2026, 6, 3, 12, tzinfo=tz), tz)
        self.assertEqual([ev['title'] for ev in events], ['Moved overnight'])

    def test_permissions_csrf_and_disabled_display(self):
        self.assertEqual(self.client.get('/admin/slideshows/main').status_code, 403)
        self.assertEqual(self.client.post('/admin/slideshows/main', data={
            'action': 'edit_builtins', 'clock_duration': '15', 'schedule_duration': '15'}).status_code, 403)
        self.admin()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/admin/slideshows/main', data={
            'action': 'edit_builtins', 'clock_duration': '15', 'schedule_duration': '15'}).status_code, 400)
        self.config['displays']['lobby']['active'] = False
        self.save()
        self.assertEqual(self.client.get('/api/display-slides/lobby').status_code, 404)
        self.assertEqual(self.client.get('/api/slideshow-slides/main').status_code, 200)


if __name__ == '__main__':
    unittest.main()
