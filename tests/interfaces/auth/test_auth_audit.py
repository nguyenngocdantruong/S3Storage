import unittest

from application import create_app
from extensions import db
from infrastructure.persistence.models import AuditLog, User


class AuthAuditTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            WTF_CSRF_ENABLED=False,
        )
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(name='Alice', email='alice@example.com', dob='2000-01-01', role='User', is_active=True)
            user.set_password('secret')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        self.client = self.app.test_client()

    def test_login_creates_audit_log(self):
        response = self.client.post('/login', data={'email': 'alice@example.com', 'password': 'secret'}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            log = AuditLog.query.filter_by(action_type='LOGIN').first()
            self.assertIsNotNone(log)
            self.assertEqual(log.user_id, self.user_id)
            self.assertIn('logged in', log.details)


if __name__ == '__main__':
    unittest.main()
