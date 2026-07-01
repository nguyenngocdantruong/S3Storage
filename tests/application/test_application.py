import unittest
from unittest.mock import MagicMock, patch

import application
from interfaces.middleware import context as middleware_context


class ApplicationTests(unittest.TestCase):
    @patch('application.run_startup_migrations')
    @patch('application.register_context')
    @patch.object(application.db, 'init_app')
    def test_create_app_registers_expected_config_and_blueprints(self, init_app, register_context, run_startup_migrations):
        app = application.create_app()
        self.assertIn('auth.login', app.view_functions)
        self.assertIn('admin.manage_users', app.view_functions)
        self.assertIn('viewer.view_file', app.view_functions)
        self.assertIn('api.check_paste_conflicts', app.view_functions)
        self.assertTrue(callable(app.config['GET_S3_CLIENT']))
        self.assertTrue(callable(app.config['GET_BUCKET_SIZE']))
        init_app.assert_called_once_with(app)
        register_context.assert_called_once()
        run_startup_migrations.assert_called_once_with(app)


class MiddlewareTests(unittest.TestCase):
    def test_template_g_returns_guest_user_when_none(self):
        wrapped = middleware_context.TemplateG(type('G', (), {'user': None})())
        self.assertEqual(wrapped.user.role, 'Guest')
        self.assertEqual(wrapped.user.id, -1)

    def test_build_quota_injector_marks_admin_unlimited(self):
        injector = middleware_context.build_quota_injector(MagicMock())
        with patch.object(middleware_context, 'g', type('G', (), {'user': type('User', (), {'role': 'Admin'})()})()):
            data = injector()
        self.assertTrue(data['quota_is_unlimited'])
        self.assertEqual(data['quota_used'], 0)


if __name__ == '__main__':
    unittest.main()
