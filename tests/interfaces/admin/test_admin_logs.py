import unittest

from application import create_app
from extensions import db
from infrastructure.persistence.models import AuditLog, User


class AdminSystemLogsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'WTF_CSRF_ENABLED': False,
        })
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            admin = User(name='Admin', email='admin@example.com', dob='2000-01-01', role='Admin', is_active=True)
            admin.set_password('secret')
            user = User(name='Alice', email='alice@example.com', dob='2000-01-01', role='User', is_active=True)
            user.set_password('secret')
            db.session.add_all([admin, user])
            db.session.commit()
            db.session.add_all([
                AuditLog(user_id=admin.id, target_user_id=admin.id, action_type='LOGIN', details='Admin logged in'),
                AuditLog(user_id=user.id, target_user_id=user.id, connection_name='conn-a', bucket_name='bucket-a', action_type='UPLOAD_FILE', details='Uploaded report.pdf'),
            ])
            db.session.commit()
            self.admin_id = admin.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = self.admin_id

    def test_system_logs_renders_audit_table(self):
        response = self.client.get('/admin/system-logs')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Triggered By', html)
        self.assertIn('UPLOAD FILE', html)
        self.assertIn('Admin', html)
        self.assertIn('Uploaded report.pdf', html)

    def test_system_logs_filters_by_action(self):
        response = self.client.get('/admin/system-logs?action=LOGIN')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Admin logged in', html)
        self.assertNotIn('Uploaded report.pdf', html)

    def test_clear_system_logs_deletes_audit_records(self):
        response = self.client.post('/admin/system-logs/clear', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(AuditLog.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
