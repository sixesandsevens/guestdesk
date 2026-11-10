"""Scheduled video programming tests use temporary JSON and database storage."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from werkzeug.datastructures import MultiDict

from guestdesk import display


class VideoProgrammingTests(unittest.TestCase):
    def setUp(self):
        from guestdesk import app as app_module, grievances
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for item in (
            patch.object(app_module, 'DATA_DIR', str(root)),
            patch.object(grievances, 'DATA_DIR', str(root)),
            patch.object(display, 'DATA_ROOT', root / 'display'),
            patch.object(display, 'DATA_PATH', root / 'display' / 'display_config.json'),
            patch.object(display, 'SLIDES_DIR', root / 'display' / 'slides'),
            patch.object(display, 'LEGACY_DATA_PATH', root / 'missing-legacy.json'),
            patch.object(app_module, 'queue_mail', lambda **kwargs: None),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.app = app_module.create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                               BABEL_DEFAULT_TIMEZONE='America/New_York')
        self.client = self.app.test_client()
        self.config = {
            'displays': {'lobby': {'name': 'Lobby', 'active': True,
                                   'assigned_slideshow': 'main'}},
            'slideshows': {'main': {'name': 'Main', 'slides': [
                {'id': 1, 'type': 'text', 'headline': 'Welcome', 'duration': 10, 'order': 1}
            ]}},
        }
        display.save_config(self.config)

    def admin(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

    def add_list(self):
        self.admin()
        response = self.client.post('/admin/video-lists', data={
            'action': 'add_list', 'name': 'Campus Videos'})
        self.assertEqual(response.status_code, 302)
        return '/admin/video-lists/campus-videos'

    def add_entry(self, list_url, title='First', video='M7lc1UVf-VE', **flags):
        form = {'action': 'add_entry', 'title': title,
                'video_url': f'https://youtu.be/{video}'}
        form.update(flags)
        return self.client.post(list_url, data=form)

    def window_form(self, **changes):
        data = MultiDict([('action', 'add_window'), ('name', 'Morning'),
                          ('video_list', 'campus-videos'), ('start', '09:00'),
                          ('end', '10:00'), ('enabled', 'on'), ('days', '0')])
        for key, value in changes.items():
            data.setlist(key, value if isinstance(value, list) else [value])
        return data

    def test_old_config_and_preview_remain_unchanged(self):
        cfg = display.load_config()
        self.assertEqual(cfg['video_lists'], {})
        self.assertNotIn('video_windows', cfg['displays']['lobby'])
        api = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual(api['video_windows'], [])
        self.assertEqual(api['display']['video_audio_enabled'], False)
        self.assertEqual(api['display']['video_volume'], 50)
        self.assertEqual([s['headline'] for s in api['slides']], ['Welcome'])
        preview = self.client.get('/slideshows/main/preview')
        self.assertNotIn(b'display_youtube.js', preview.data)
        self.assertEqual(self.client.get('/api/slideshow-slides/main').get_json()['slides'],
                         api['slides'])

    def test_entry_review_eligibility_reorder_and_changed_video(self):
        url = self.add_list()
        self.add_entry(url, title='<script>alert(1)</script>', enabled='on')
        self.add_entry(url, title='Second', video='dQw4w9WgXcQ',
                       enabled='on', reviewed='on')
        cfg = display.load_config()
        entries = cfg['video_lists']['campus-videos']['entries']
        self.assertEqual(len(display.eligible_videos(cfg['video_lists']['campus-videos'])), 1)
        self.assertIn(b'&lt;script&gt;', self.client.get(url).data)
        self.assertNotIn(b'<script>alert(1)</script>', self.client.get(url).data)
        self.client.post(url, data={'action': 'move_up', 'entry_id': entries[1]['id']})
        entries = display.load_config()['video_lists']['campus-videos']['entries']
        self.assertEqual(entries[0]['title'], 'Second')
        self.client.post(url, data={'action': 'edit_entry', 'entry_id': entries[0]['id'],
                                    'title': 'Second', 'video_url': 'https://youtu.be/M7lc1UVf-VE',
                                    'enabled': 'on', 'reviewed': 'on'})
        entry = display.load_config()['video_lists']['campus-videos']['entries'][0]
        self.assertFalse(entry['reviewed'])
        self.assertEqual(display.eligible_videos(display.load_config()['video_lists']['campus-videos']), [])

    def test_window_overlap_adjacency_and_api_projection(self):
        url = self.add_list()
        self.add_entry(url, enabled='on', reviewed='on')
        self.add_entry(url, title='Unchecked', video='dQw4w9WgXcQ', enabled='on')
        programming = '/admin/displays/lobby/programming'
        self.client.post(programming, data=self.window_form())
        page = self.client.get(programming)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'America/New_York', page.data)
        cfg = display.load_config()
        first = cfg['displays']['lobby']['video_windows'][0]
        self.assertEqual(first['days'], [0])
        self.client.post(programming, data=self.window_form(name='Overlap', start='09:30'))
        self.assertEqual(len(display.load_config()['displays']['lobby']['video_windows']), 1)
        self.client.post(programming, data=self.window_form(name='Adjacent', start='10:00', end='11:00'))
        self.assertEqual(len(display.load_config()['displays']['lobby']['video_windows']), 2)
        self.client.post(programming, data=self.window_form(name='Different day',
                         days=['1'], start='09:30'))
        self.assertEqual(len(display.load_config()['displays']['lobby']['video_windows']), 3)
        self.client.post(programming, data=self.window_form(name='Disabled overlap',
                         enabled=[], start='09:30'))
        self.assertEqual(len(display.load_config()['displays']['lobby']['video_windows']), 4)
        api = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual(len(api['video_windows']), 3)
        self.assertEqual([v['video_id'] for v in api['video_windows'][0]['videos']], ['M7lc1UVf-VE'])
        self.assertNotIn('video_windows', api['display'])
        self.assertEqual(api['timezone'], 'America/New_York')
        self.assertNotIn('video_windows', self.client.get('/api/slideshow-slides/main').get_json())
        self.client.post('/admin/video-lists', data={'action': 'delete_list', 'slug': 'campus-videos'})
        self.assertIn('campus-videos', display.load_config()['video_lists'])

    def test_invalid_times_days_and_references_do_not_save(self):
        self.add_list()
        route = '/admin/displays/lobby/programming'
        for changes in ({'end': '09:00'}, {'end': '08:00'},
                        {'start': '25:00'}, {'days': []},
                        {'days': ['8']}, {'video_list': 'missing'}):
            with self.subTest(changes=changes):
                self.client.post(route, data=self.window_form(**changes))
                self.assertEqual(display.load_config()['displays']['lobby'].get('video_windows', []), [])

    def test_display_sound_settings_are_independent_and_validated(self):
        self.admin()
        cfg = display.load_config()
        cfg['displays']['hall'] = {'name': 'Hall', 'active': True,
                                   'assigned_slideshow': 'main'}
        display.save_config(cfg)
        route = '/admin/displays/lobby/programming'
        self.client.post(route, data={'action': 'audio_settings',
                                      'video_audio_enabled': 'on', 'video_volume': '35'})
        saved = display.load_config()['displays']['lobby']
        self.assertTrue(saved['video_audio_enabled'])
        self.assertEqual(saved['video_volume'], 35)
        api = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual((api['display']['video_audio_enabled'], api['display']['video_volume']),
                         (True, 35))
        hall = self.client.get('/api/display-slides/hall').get_json()['display']
        self.assertEqual((hall['video_audio_enabled'], hall['video_volume']), (False, 50))
        self.assertIn(b'Play scheduled videos with sound', self.client.get(route).data)
        for invalid in ('0', '101', '1.5', 'loud'):
            with self.subTest(invalid=invalid):
                self.client.post(route, data={'action': 'audio_settings',
                                              'video_volume': invalid})
                current = display.load_config()['displays']['lobby']
                self.assertEqual((current['video_audio_enabled'], current['video_volume']),
                                 (True, 35))
        self.client.post(route, data={'action': 'audio_settings', 'video_volume': '35'})
        self.assertFalse(display.load_config()['displays']['lobby']['video_audio_enabled'])
        self.assertEqual(self.client.get('/api/slideshow-slides/main').get_json()['slides'],
                         api['slides'])

    def test_boundaries_weekdays_and_dst(self):
        ny = ZoneInfo('America/New_York')
        windows = [{'id': 'a', 'enabled': True, 'days': [6],
                    'start': '01:00', 'end': '02:00'}]
        # Both 1:30 occurrences on fall-back Sunday are inside the window.
        for fold in (0, 1):
            at = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=fold)
            self.assertEqual(display.active_video_window(windows, at)['id'], 'a')
        self.assertIsNone(display.active_video_window(windows,
            datetime(2026, 11, 1, 2, 0, tzinfo=ny)))
        self.assertIsNone(display.active_video_window(windows,
            datetime(2026, 11, 2, 1, 30, tzinfo=ny)))
        spring = [{'id': 'b', 'enabled': True, 'days': [6],
                   'start': '01:30', 'end': '03:30'}]
        self.assertIsNotNone(display.active_video_window(spring,
            datetime(2026, 3, 8, 3, 0, tzinfo=ny)))
        self.assertIsNone(display.active_video_window(spring,
            datetime(2026, 3, 8, 3, 30, tzinfo=ny)))

    def test_missing_list_empty_fallback_permissions_and_csrf(self):
        self.assertEqual(self.client.get('/admin/video-lists').status_code, 403)
        self.assertEqual(self.client.post('/admin/displays/lobby/programming').status_code, 403)
        self.assertEqual(self.client.post('/admin/displays/lobby/programming', data={
            'action': 'audio_settings', 'video_audio_enabled': 'on', 'video_volume': '35'
        }).status_code, 403)
        self.admin()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/admin/displays/lobby/programming', data={
            'action': 'audio_settings', 'video_audio_enabled': 'on', 'video_volume': '35'
        }).status_code, 400)
        self.assertEqual(self.client.post('/admin/video-lists', data={
            'action': 'add_list', 'name': 'Videos'}).status_code, 400)
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.config['displays']['lobby'].update(video_windows=[
            {'id': 'x', 'name': 'Broken', 'enabled': True, 'days': [0],
             'start': '09:00', 'end': '10:00', 'video_list': 'deleted'}])
        self.config['displays']['lobby']['assigned_slideshow'] = None
        display.save_config(self.config)
        api = self.client.get('/api/display-slides/lobby').get_json()
        self.assertEqual(api['video_windows'][0]['videos'], [])
        self.assertEqual(api['slides'], [])
        self.config['displays']['lobby']['active'] = False
        display.save_config(self.config)
        self.assertEqual(self.client.get('/api/display-slides/lobby').status_code, 404)


if __name__ == '__main__':
    unittest.main()
