"""The YouTube device-test milestone uses temporary display configuration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guestdesk import display


class YouTubeTestPageTests(unittest.TestCase):
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
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()
        self.config = {
            'displays': {
                'lobby': {'name': 'Lobby', 'active': True, 'assigned_slideshow': 'main'},
                'closed': {'name': 'Closed', 'active': False, 'assigned_slideshow': 'main'},
            },
            'slideshows': {'main': {'name': 'Main', 'slides': [
                {'id': 1, 'type': 'text', 'headline': 'Welcome', 'duration': 10, 'order': 1}
            ]}},
        }
        display.save_config(self.config)

    def admin(self):
        with self.client.session_transaction() as session:
            session['is_admin'] = True

    def test_url_formats_and_rejected_hosts(self):
        video = 'M7lc1UVf-VE'
        for url in (
            f'https://www.youtube.com/watch?v={video}&t=10',
            f'https://m.youtube.com/watch?v={video}',
            f'https://youtu.be/{video}?si=test',
            f'https://www.youtube.com/embed/{video}',
            f'https://www.youtube-nocookie.com/embed/{video}',
            f'https://youtube.com/shorts/{video}',
        ):
            self.assertEqual(display.youtube_video_id(url), video)
        for url in (
            'https://youtube.com.evil.test/watch?v=M7lc1UVf-VE',
            'javascript:alert(1)',
            'https://www.youtube.com/watch?v=invalid',
            'https://youtu.be/M7lc1UVf-VE/extra',
            'https://www.youtube.com:bad/watch?v=M7lc1UVf-VE',
            '<iframe src="https://www.youtube.com/embed/M7lc1UVf-VE"></iframe>',
            'https://www.youtube.com/playlist?list=abc',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                display.youtube_video_id(url)

    def test_staff_route_does_not_change_config_and_escapes_display_name(self):
        self.config['displays']['lobby']['name'] = '<script>alert(1)</script>'
        display.save_config(self.config)
        before = display.DATA_PATH.read_bytes()
        self.admin()
        response = self.client.get('/admin/displays/youtube-test')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        self.assertNotIn(b'Closed', response.data)
        response = self.client.post('/admin/displays/youtube-test', data={
            'video_url': 'https://youtu.be/M7lc1UVf-VE', 'display_slug': 'lobby'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('video_id=M7lc1UVf-VE', response.headers['Location'])
        player = self.client.get(response.headers['Location'])
        self.assertEqual(player.status_code, 200)
        self.assertIn(b'display_youtube.js', player.data)
        self.assertIn(b'/displays/lobby', player.data)
        self.assertIn(b'/displays/lobby?preview=1', player.data)
        self.assertIsNone(player.headers.get('Content-Security-Policy'))
        self.assertEqual(player.headers.get('Referrer-Policy'),
                         'strict-origin-when-cross-origin')
        self.assertEqual(display.DATA_PATH.read_bytes(), before)
        self.assertEqual(self.client.get('/api/display-slides/lobby').status_code, 200)
        self.assertNotIn(b'www.youtube.com/iframe_api', self.client.get('/displays/lobby').data)

    def test_explicit_sound_test_does_not_save_display_settings(self):
        self.admin()
        before = display.DATA_PATH.read_bytes()
        response = self.client.post('/admin/displays/youtube-test', data={
            'video_url': 'https://youtu.be/M7lc1UVf-VE', 'display_slug': 'lobby',
            'video_audio_enabled': 'on', 'video_volume': '40'})
        self.assertEqual(response.status_code, 302)
        player = self.client.get(response.headers['Location'])
        self.assertIn(b'"audioEnabled": true', player.data)
        self.assertIn(b'"volume": 40', player.data)
        self.assertEqual(display.DATA_PATH.read_bytes(), before)
        response = self.client.post('/admin/displays/youtube-test', data={
            'video_url': 'https://youtu.be/M7lc1UVf-VE', 'display_slug': 'lobby',
            'video_volume': '101'})
        self.assertEqual(response.headers['Location'], '/admin/displays/youtube-test')
        self.assertEqual(self.client.get(
            '/admin/displays/youtube-test?video_id=M7lc1UVf-VE&display_slug=lobby&volume=0'
        ).status_code, 400)

    def test_invalid_video_and_disabled_display(self):
        self.admin()
        for url, slug in (("https://evil.test/watch?v=M7lc1UVf-VE", 'lobby'),
                          ('https://youtu.be/M7lc1UVf-VE', 'closed')):
            response = self.client.post('/admin/displays/youtube-test', data={
                'video_url': url, 'display_slug': slug})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers['Location'], '/admin/displays/youtube-test')
        self.assertEqual(self.client.get(
            '/admin/displays/youtube-test?video_id=invalid&display_slug=lobby').status_code, 400)

    def test_permissions_and_csrf(self):
        self.assertEqual(self.client.get('/admin/displays/youtube-test').status_code, 403)
        self.assertEqual(self.client.post('/admin/displays/youtube-test', data={}).status_code, 403)
        self.admin()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/admin/displays/youtube-test', data={
            'video_url': 'https://youtu.be/M7lc1UVf-VE', 'display_slug': 'lobby'
        }).status_code, 400)


if __name__ == '__main__':
    unittest.main()
