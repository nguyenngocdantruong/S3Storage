import configparser
import os
from datetime import timedelta


class Config:
    def __init__(self, project_root=None):
        self.project_root = project_root or os.path.abspath(os.path.dirname(__file__))
        self.config_path = os.path.join(self.project_root, 'config.conf')
        self.secret_key_path = os.path.join(self.project_root, '.secret_key')
        self.db_path = os.getenv('DATABASE_PATH', os.path.join(self.project_root, 's3player.db'))
        self.session_lifetime_days = int(os.getenv('SESSION_LIFETIME_DAYS', '90'))
        self._parser = configparser.ConfigParser()
        if os.path.exists(self.config_path):
            self._parser.read(self.config_path, encoding='utf-8')

    def _load_secret_key(self):
        secret_from_env = os.getenv('SECRET_KEY')
        if secret_from_env:
            return secret_from_env

        if os.path.exists(self.secret_key_path):
            with open(self.secret_key_path, 'rb') as f:
                return f.read()

        generated_key = os.urandom(24)
        with open(self.secret_key_path, 'wb') as f:
            f.write(generated_key)
        return generated_key

    def apply(self, app):
        app.secret_key = self._load_secret_key()
        app.config['PROJECT_ROOT'] = self.project_root
        app.config['CONFIG_PATH'] = self.config_path
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=self.session_lifetime_days)
        app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{self.db_path}"
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        app.config['APP_CONFIG'] = self
        return app

    @property
    def admin(self):
        section = self._parser['ADMIN'] if 'ADMIN' in self._parser else {}
        return {
            'email': os.getenv('ADMIN_EMAIL', section.get('email', 'admin@example.com')),
            'password': os.getenv('ADMIN_PASSWORD', section.get('password', 'admin123')),
            'fullname': os.getenv('ADMIN_FULLNAME', section.get('fullname', 'Administrator')),
            'dob': os.getenv('ADMIN_DOB', section.get('dob', '1990-01-01')),
        }
