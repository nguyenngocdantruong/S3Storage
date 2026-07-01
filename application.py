import logging
import os
import traceback
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, render_template, request

from config import Config
from extensions import db
from infrastructure.persistence.migrations import run_startup_migrations
from infrastructure.storage.boto3_provider import fix_url, get_storage_provider
from interfaces.admin.views import bp as admin_bp
from interfaces.api.views import bp as api_bp
from interfaces.auth.views import bp as auth_bp
from interfaces.buckets.views import bp as buckets_bp
from interfaces.connections.views import bp as connections_bp
from interfaces.files.views import bp as files_bp
from interfaces.main.views import bp as main_bp
from interfaces.middleware.context import register_context
from interfaces.progress.views import bp as progress_bp
from interfaces.viewer.views import bp as viewer_bp
from use_cases.quota import get_user_storage_used as quota_get_user_storage_used


def fix_s3_url(url):
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
    return fix_url(url, is_https)



def get_s3_client(connection, endpoint_url=None):
    return get_storage_provider(connection, endpoint_url=endpoint_url)



def configure_bucket_cors(s3_client, bucket_name):
    cors_configuration = {
        'CORSRules': [
            {
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['GET', 'PUT', 'POST', 'DELETE', 'HEAD'],
                'AllowedOrigins': ['*'],
                'MaxAgeSeconds': 3000,
            }
        ]
    }
    try:
        s3_client.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_configuration)
    except Exception as e:
        print(f'Error configuring CORS for bucket {bucket_name}: {e}')



def get_bucket_size(s3_client, bucket_name):
    total_size = 0
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name)
        for page in pages:
            for obj in page.get('Contents', []):
                total_size += obj.get('Size', 0)
    except Exception:
        return None
    return total_size



def create_app():
    app = Flask(__name__)
    project_root = os.path.abspath(os.path.dirname(__file__))

    log_file_path = os.path.join(project_root, 'system.log')
    file_handler = RotatingFileHandler(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

    logging.getLogger('werkzeug').addHandler(file_handler)
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(logging.INFO)

    Config(project_root).apply(app)
    db.init_app(app)

    @app.errorhandler(400)
    def bad_request(e):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': 'Bad Request'}), 400
        return render_template('error.html', code=400, title='Bad Request', message='The request could not be understood or is missing required parameters.'), 400

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
        return render_template('error.html', code=403, title='Forbidden', message='You do not have permission to access this resource.'), 403

    @app.errorhandler(404)
    def page_not_found(e):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': 'Not Found'}), 404
        return render_template('error.html', code=404, title='Page Not Found', message='The page or resource you are looking for does not exist or has been moved.'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': 'Internal Server Error'}), 500
        return render_template('error.html', code=500, title='Internal Server Error', message='An unexpected error occurred on the server. Please try again later.'), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.error('System Exception: %s\n%s', str(e), traceback.format_exc())
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': e.description}), e.code
            return render_template('error.html', code=e.code, title=e.name, message=e.description), e.code
        if request.path.startswith('/api/'):
            return jsonify({'status': 'error', 'message': str(e)}), 500
        return render_template('error.html', code=500, title='Internal Server Error', message='An unexpected error occurred. Please contact the administrator.'), 500

    app.config['GET_S3_CLIENT'] = get_s3_client
    app.config['GET_BUCKET_SIZE'] = get_bucket_size
    app.config['FIX_S3_URL'] = fix_s3_url
    app.config['CONFIGURE_BUCKET_CORS'] = configure_bucket_cors
    app.config['GET_USER_STORAGE_USED'] = (
        lambda user: quota_get_user_storage_used(
            user,
            db_session=db.session,
            storage_provider_factory=get_s3_client,
        )
    )

    register_context(app, get_s3_client)

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(connections_bp)
    app.register_blueprint(buckets_bp)
    app.register_blueprint(viewer_bp)
    app.register_blueprint(progress_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(api_bp)

    run_startup_migrations(app)

    return app
