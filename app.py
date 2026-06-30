import os
import urllib.parse
import configparser
from datetime import datetime, timedelta
from functools import wraps
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import logging
from logging.handlers import RotatingFileHandler
import traceback
import threading
import io
from PIL import Image
import uuid
import yt_dlp

app = Flask(__name__)

# Configure Logging to file
log_file_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'system.log')
file_handler = RotatingFileHandler(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(logging.Formatter(
    '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

# Also capture server requests (werkzeug) and general root logs
logging.getLogger('werkzeug').addHandler(file_handler)
logging.getLogger().addHandler(file_handler)
logging.getLogger().setLevel(logging.INFO)

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error("System Exception: %s\n%s", str(e), traceback.format_exc())
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': str(e)}), 500
    return e
secret_key_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '.secret_key')
if os.path.exists(secret_key_path):
    with open(secret_key_path, 'rb') as f:
        app.secret_key = f.read()
else:
    generated_key = os.urandom(24)
    with open(secret_key_path, 'wb') as f:
        f.write(generated_key)
    app.secret_key = generated_key
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)

# Database Configuration (SQLite)
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 's3player.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dob = db.Column(db.String(50)) # Date of Birth
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='User') # 'Admin' or 'User'
    quota_limit = db.Column(db.BigInteger, default=2147483648) # 2GB default
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class S3Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    endpoint_url = db.Column(db.String(255), nullable=False)
    access_key = db.Column(db.String(255), nullable=False)
    secret_key = db.Column(db.String(255), nullable=False)
    region_name = db.Column(db.String(100), default='us-east-1')
    upload_endpoint = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<S3Connection {self.name}>'

class UserBucket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey('s3_connection.id'), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    access_type = db.Column(db.String(20), default='restricted') # 'restricted' or 'public'
    bucket_size = db.Column(db.BigInteger, default=0, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('owned_buckets', lazy=True))
    connection = db.relationship('S3Connection', backref=db.backref('mapped_buckets', lazy=True))

class BucketAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey('s3_connection.id'), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='Viewer') # 'Viewer' or 'Editor'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('shared_accesses', cascade='all, delete-orphan'))
    connection = db.relationship('S3Connection', backref=db.backref('shared_accesses', cascade='all, delete-orphan'))

class VideoProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connection_name = db.Column(db.String(100), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    seconds_watched = db.Column(db.Float, default=0.0)
    duration = db.Column(db.Float, default=0.0)
    last_watched_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('video_progresses', lazy=True))

class VideoNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connection_name = db.Column(db.String(100), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.Float, nullable=False)  # The video playback seconds
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('video_notes', lazy=True))

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Who did it
    target_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # Resource owner
    connection_name = db.Column(db.String(100))
    bucket_name = db.Column(db.String(100))
    action_type = db.Column(db.String(50)) # 'CREATE_BUCKET', 'DELETE_BUCKET', 'UPLOAD_FILE', 'DELETE_FILE'
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    actor = db.relationship('User', foreign_keys=[user_id], backref=db.backref('actions_logged', lazy=True))
    target_owner = db.relationship('User', foreign_keys=[target_user_id], backref=db.backref('target_logs', lazy=True))

class UploadedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('s3_connection.id'), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    file_key = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('uploaded_files', lazy=True))
    connection = db.relationship('S3Connection', backref=db.backref('uploaded_files', lazy=True))

class RemoteTask(db.Model):
    id = db.Column(db.String(50), primary_key=True) # UUID
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connection_id = db.Column(db.Integer, db.ForeignKey('s3_connection.id'), nullable=False)
    bucket_name = db.Column(db.String(100), nullable=False)
    prefix = db.Column(db.String(255), default='')
    link = db.Column(db.Text, nullable=False)
    link_type = db.Column(db.String(20)) # 'video', 'direct', 'torrent'
    status = db.Column(db.String(20), default='pending') # 'pending', 'downloading', 'uploading', 'completed', 'failed'
    progress = db.Column(db.Integer, default=0)
    speed = db.Column(db.String(50), default='')
    filename = db.Column(db.String(255), default='')
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('remote_tasks', lazy=True))
    connection = db.relationship('S3Connection', backref=db.backref('remote_tasks', lazy=True))


# Helper: Fix S3 URL for Mixed Content issues (HTTPS -> HTTPS)
def fix_s3_url(url):
    if not url:
        return url
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
    if is_https and url.startswith('http://'):
        return url.replace('http://', 'https://', 1)
    return url

# Helper: Get boto3 client
def get_s3_client(connection, endpoint_url=None):
    config = Config(
        signature_version='s3v4',
        retries={'max_attempts': 3},
        s3={'addressing_style': 'path'}
    )
    if endpoint_url is None:
        endpoint_url = connection.endpoint_url
    endpoint = endpoint_url if (endpoint_url and endpoint_url.strip()) else None
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=connection.access_key,
        aws_secret_access_key=connection.secret_key,
        region_name=connection.region_name or 'us-east-1',
        config=config
    )

# Helper: Configure CORS for direct browser upload
def configure_bucket_cors(s3_client, bucket_name):
    cors_configuration = {
        'CORSRules': [
            {
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['GET', 'PUT', 'POST', 'DELETE', 'HEAD'],
                'AllowedOrigins': ['*'],
                'MaxAgeSeconds': 3000
            }
        ]
    }
    try:
        s3_client.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_configuration)
    except Exception as e:
        print(f"Error configuring CORS for bucket {bucket_name}: {e}")

# Helper to get size of a single bucket
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

# Storage calculator
def get_user_storage_used(user):
    user_buckets = UserBucket.query.filter_by(user_id=user.id).all()
    total_size = 0
    client_cache = {}
    
    for ub in user_buckets:
        conn = db.session.get(S3Connection, ub.connection_id)
        if not conn:
            continue
        try:
            if conn.id not in client_cache:
                client_cache[conn.id] = get_s3_client(conn)
            s3 = client_cache[conn.id]
            
            paginator = s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=ub.bucket_name)
            for page in pages:
                for obj in page.get('Contents', []):
                    total_size += obj.get('Size', 0)
        except Exception:
            pass # Ignore offline S3 connections during calculation
            
    return total_size

def check_bucket_access(user, connection, bucket_name):
    # Check if bucket is public (Anyone with the link)
    mapping = UserBucket.query.filter_by(connection_id=connection.id, bucket_name=bucket_name).first()
    if mapping and mapping.access_type in ['public', 'public_view', 'public_edit']:
        return True
        
    if not user:
        return False
    if user.role == 'Admin':
        return True
    # Check ownership mapping
    if mapping and mapping.user_id == user.id:
        return True
    # Check shared access mapping (Viewer or Editor role)
    shared = BucketAccess.query.filter_by(
        user_id=user.id,
        connection_id=connection.id,
        bucket_name=bucket_name
    ).first()
    if shared:
        return True
    return False

def check_bucket_edit_access(user, connection, bucket_name):
    if not user:
        return False
    if user.role == 'Admin':
        return True
    # Check ownership mapping
    mapping = UserBucket.query.filter_by(connection_id=connection.id, bucket_name=bucket_name).first()
    if mapping and mapping.user_id == user.id:
        return True
    # If bucket general access is set to public_edit, any logged-in user can edit
    if mapping and mapping.access_type == 'public_edit':
        return True
    # Check shared access mapping with role 'Editor'
    shared = BucketAccess.query.filter_by(
        user_id=user.id,
        connection_id=connection.id,
        bucket_name=bucket_name
    ).first()
    if shared and shared.role == 'Editor':
        return True
    return False

def generate_and_cache_preview_task(app_to_use, connection_id, bucket_name, key, file_type):
    import urllib.request
    with app_to_use.app_context():
        try:
            conn = db.session.get(S3Connection, connection_id)
            if not conn:
                return
            s3 = get_s3_client(conn)
            
            preview_bucket = 'preview-image'
            try:
                s3.head_bucket(Bucket=preview_bucket)
            except ClientError as e:
                # Bucket doesn't exist, create it
                error_code = e.response.get('Error', {}).get('Code')
                if error_code in ['404', 'NoSuchBucket']:
                    kwargs = {'Bucket': preview_bucket}
                    if conn.region_name and conn.region_name != 'us-east-1' and 'amazonaws.com' in conn.endpoint_url:
                        kwargs['CreateBucketConfiguration'] = {'LocationConstraint': conn.region_name}
                    s3.create_bucket(**kwargs)
                    configure_bucket_cors(s3, preview_bucket)
                else:
                    app_to_use.logger.error(f"Error checking/creating preview bucket: {e}")
                    return
            
            preview_key = f"{bucket_name}/{key}.jpg"
            
            if file_type == 'image':
                response = s3.get_object(Bucket=bucket_name, Key=key)
                img_data = response['Body'].read()
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((480, 480))
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=85)
                output.seek(0)
                s3.put_object(
                    Bucket=preview_bucket,
                    Key=preview_key,
                    Body=output,
                    ContentType='image/jpeg'
                )

        except Exception as ex:
            app_to_use.logger.error(f"Error in generate_and_cache_preview_task for connection {connection_id}, bucket {bucket_name}, key {key}: {ex}\n{traceback.format_exc()}")

def log_action(actor_id, target_user_id, connection_name, bucket_name, action_type, details):
    try:
        log = AuditLog(
            user_id=actor_id,
            target_user_id=target_user_id,
            connection_name=connection_name,
            bucket_name=bucket_name,
            action_type=action_type,
            details=details
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"Log Error: {e}")

# Create tables and default Admin, with soft migration check
with app.app_context():
    db_exists = os.path.exists(db_path)
    db.create_all()
    try:
        db.session.execute(db.text("PRAGMA journal_mode=WAL;"))
        db.session.commit()
    except Exception as e:
        print(f"Failed to set WAL mode: {e}")
    # Migration: check if remote_task table exists, if not create all
    try:
        db.session.execute(db.text("SELECT id FROM remote_task LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.create_all()
        except Exception as e:
            print(f"Migration error for remote_task: {e}")

    # Migration: check if user table has quota_limit column
    try:
        db.session.execute(db.text("SELECT quota_limit FROM user LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE user ADD COLUMN quota_limit INTEGER DEFAULT 2147483648"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error: {e}")

    # Migration: check if s3_connection table has connection_id column
    try:
        db.session.execute(db.text("SELECT connection_id FROM s3_connection LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE s3_connection ADD COLUMN connection_id VARCHAR(100)"))
            db.session.commit()
            # Generate slugs for existing connections
            conns = S3Connection.query.all()
            for c in conns:
                slug = c.name.lower().strip().replace(' ', '-')
                # Remove non-alphanumeric chars except dashes
                import re
                slug = re.sub(r'[^a-z0-9\-]', '', slug)
                if not slug:
                    slug = f"conn-{c.id}"
                base_slug = slug
                count = 1
                while S3Connection.query.filter_by(connection_id=slug).first():
                    slug = f"{base_slug}-{count}"
                    count += 1
                c.connection_id = slug
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error for s3_connection: {e}")

    # Migration: check if s3_connection table has upload_endpoint column
    try:
        db.session.execute(db.text("SELECT upload_endpoint FROM s3_connection LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE s3_connection ADD COLUMN upload_endpoint VARCHAR(255)"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error for upload_endpoint: {e}")

    # Migration: check if user_bucket table has access_type column
    try:
        db.session.execute(db.text("SELECT access_type FROM user_bucket LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE user_bucket ADD COLUMN access_type VARCHAR(20) DEFAULT 'restricted'"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error for user_bucket: {e}")

    # Migration: check if user_bucket table has bucket_size column
    try:
        db.session.execute(db.text("SELECT bucket_size FROM user_bucket LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE user_bucket ADD COLUMN bucket_size BIGINT DEFAULT 0"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error for user_bucket bucket_size: {e}")

    # Migration: check if bucket_access table has role column
    try:
        db.session.execute(db.text("SELECT role FROM bucket_access LIMIT 1")).fetchone()
    except Exception:
        db.session.rollback()
        try:
            db.session.execute(db.text("ALTER TABLE bucket_access ADD COLUMN role VARCHAR(20) DEFAULT 'Viewer'"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error for bucket_access: {e}")

    # Check if DB was just created or if there are no users at all
    db_is_empty = False
    try:
        db_is_empty = (User.query.count() == 0)
    except Exception:
        db_is_empty = True

    if not db_exists or db_is_empty:
        # Default fallback credentials
        admin_email = 'admin@example.com'
        admin_password = 'admin123'
        admin_name = 'Administrator'
        admin_dob = '1990-01-01'

        config_file = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config.conf')
        if os.path.exists(config_file):
            try:
                cp = configparser.ConfigParser()
                cp.read(config_file)
                if 'ADMIN' in cp:
                    admin_email = cp['ADMIN'].get('email', admin_email)
                    admin_password = cp['ADMIN'].get('password', admin_password)
                    admin_name = cp['ADMIN'].get('fullname', admin_name)
                    admin_dob = cp['ADMIN'].get('dob', admin_dob)
            except Exception as e:
                print(f"Error parsing config.conf: {e}")

        admin = User(name=admin_name, email=admin_email, dob=admin_dob, role="Admin")
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()

    # Legacy/Unassigned bucket sync to Admin on startup
    try:
        admin_user = User.query.filter_by(role='Admin').first()
        if admin_user:
            connections = S3Connection.query.all()
            for conn in connections:
                try:
                    s3 = get_s3_client(conn)
                    response = s3.list_buckets()
                    for bucket in response.get('Buckets', []):
                        bucket_name = bucket['Name']
                        mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
                        if not mapping:
                            size = get_bucket_size(s3, bucket_name) or 0
                            new_mapping = UserBucket(
                                user_id=admin_user.id,
                                connection_id=conn.id,
                                bucket_name=bucket_name,
                                access_type='restricted',
                                bucket_size=size
                            )
                            db.session.add(new_mapping)
                            print(f"Startup Sync: Mapped unassigned bucket '{bucket_name}' to Admin '{admin_user.name}' with size {size}")
                        else:
                            # Update size of legacy bucket mapped to Admin at startup
                            if mapping.user_id == admin_user.id:
                                size = get_bucket_size(s3, bucket_name) or 0
                                mapping.bucket_size = size
                                print(f"Startup Sync: Calculated and updated legacy bucket '{bucket_name}' size to {size}")
                    db.session.commit()
                except Exception as conn_err:
                    print(f"Startup Sync Warning: Failed to scan connection {conn.name}: {conn_err}")
    except Exception as sync_err:
        print(f"Startup Sync Error: {sync_err}")

# Middleware & Context injection
@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        g.user = db.session.get(User, user_id)

@app.context_processor
def inject_quota():
    if g.user:
        if g.user.role == 'Admin':
            return {
                'quota_used': 0,
                'quota_limit': 0,
                'quota_pct': 0,
                'quota_is_unlimited': True
            }
        used = get_user_storage_used(g.user)
        limit = g.user.quota_limit or 2147483648
        pct = round(used / limit * 100, 1) if limit > 0 else 0
        return {
            'quota_used': used,
            'quota_limit': limit,
            'quota_pct': pct,
            'quota_is_unlimited': False
        }
    return {
        'quota_used': 0,
        'quota_limit': 0,
        'quota_pct': 0,
        'quota_is_unlimited': True
    }

class TemplateG:
    def __init__(self, original_g):
        self._g = original_g
    @property
    def user(self):
        if self._g.user is None:
            class GuestUser:
                id = -1
                name = "Guest"
                role = "Guest"
                email = ""
            return GuestUser()
        return self._g.user
    def __getattr__(self, name):
        return getattr(self._g, name)

@app.context_processor
def inject_g():
    return {'g': TemplateG(g)}

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped_view

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        if g.user.role != 'Admin':
            flash('Admin permissions required for this action.', 'error')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped_view

# --- Authentication Routes ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        dob = request.form.get('dob')
        password = request.form.get('password')
        role = 'User'

        if not all([name, email, password]):
            flash('Please fill in all required fields.', 'error')
            return render_template('register.html')

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered.', 'error')
            return render_template('register.html')

        new_user = User(name=name, email=email, dob=dob, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session.clear()
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            else:
                session.permanent = False
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name')
        dob = request.form.get('dob')
        email = request.form.get('email')
        password = request.form.get('password')

        if not name or not email:
            flash('Name and Email are required.', 'error')
            return render_template('profile.html')

        if email != g.user.email:
            existing_user = User.query.filter_by(email=email).first()
            if existing_user:
                flash('Email already in use.', 'error')
                return render_template('profile.html')
            g.user.email = email

        g.user.name = name
        g.user.dob = dob

        if password:
            g.user.set_password(password)

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html')

# --- S3 Dashboard & Connection Routes ---

@app.route('/')
def dashboard():
    connections = S3Connection.query.order_by(S3Connection.created_at.desc()).all()
    return render_template('dashboard.html', connections=connections)

@app.route('/connection/add', methods=['POST'])
@admin_required
def add_connection():
    connection_id = request.form.get('connection_id', '').strip()
    name = request.form.get('name')
    endpoint_url = request.form.get('endpoint_url')
    access_key = request.form.get('access_key')
    secret_key = request.form.get('secret_key')
    region_name = request.form.get('region_name', 'us-east-1')
    upload_endpoint = request.form.get('upload_endpoint', '').strip() or None

    if not all([name, access_key, secret_key]):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': 'Please fill in Name, Access Key, and Secret Key.'}), 400
        flash('Please fill in Name, Access Key, and Secret Key.', 'error')
        return redirect(url_for('dashboard'))

    import re
    if not connection_id:
        connection_id = name.lower().strip().replace(' ', '-')
        connection_id = re.sub(r'[^a-z0-9\-]', '', connection_id)
        if not connection_id:
            connection_id = 'conn-' + os.urandom(4).hex()
    else:
        connection_id = connection_id.lower().strip().replace(' ', '-')
        connection_id = re.sub(r'[^a-z0-9\-]', '', connection_id)

    # Ensure unique slug
    base_slug = connection_id
    count = 1
    while S3Connection.query.filter_by(connection_id=connection_id).first():
        connection_id = f"{base_slug}-{count}"
        count += 1

    try:
        conn_temp = S3Connection(
            connection_id=connection_id,
            name=name, endpoint_url=endpoint_url,
            access_key=access_key, secret_key=secret_key,
            region_name=region_name,
            upload_endpoint=upload_endpoint
        )
        s3 = get_s3_client(conn_temp)
        
        # Test connection first before saving
        try:
            s3.list_buckets()
        except ClientError as ce:
            status_code = ce.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            error_code = ce.response.get('Error', {}).get('Code')
            if status_code != 403 and error_code not in ['AccessDenied', 'AllAccessDisabled']:
                raise ce
        
        db.session.add(conn_temp)
        db.session.commit()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'success', 'message': 'S3 Connection added successfully!'})
            
        flash('S3 Connection added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': f'S3 connection test error: {error_msg}'}), 400
        flash(f'S3 connection test error: {error_msg}', 'error')

    return redirect(url_for('dashboard'))

@app.route('/connection/<connection_id>/delete', methods=['POST'])
@admin_required
def delete_connection(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    try:
        # Also clean up bucket maps, bucket access maps, and progress records
        UserBucket.query.filter_by(connection_id=conn.id).delete()
        BucketAccess.query.filter_by(connection_id=conn.id).delete()
        VideoProgress.query.filter_by(connection_name=conn.name).delete()
        UploadedFile.query.filter_by(connection_id=conn.id).delete()
        db.session.delete(conn)
        db.session.commit()
        flash('Connection deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/connection/<connection_id>/edit', methods=['POST'])
@admin_required
def edit_connection(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    name = request.form.get('name')
    endpoint_url = request.form.get('endpoint_url')
    access_key = request.form.get('access_key')
    secret_key = request.form.get('secret_key')
    region_name = request.form.get('region_name', 'us-east-1')
    upload_endpoint = request.form.get('upload_endpoint', '').strip() or None

    if not all([name, access_key, secret_key]):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': 'Please fill in Name, Access Key, and Secret Key.'}), 400
        flash('Please fill in Name, Access Key, and Secret Key.', 'error')
        return redirect(url_for('dashboard'))

    try:
        old_name = conn.name
        
        # Test connection first using a temporary object with new credentials
        conn_test = S3Connection(
            name=name, endpoint_url=endpoint_url,
            access_key=access_key, secret_key=secret_key,
            region_name=region_name,
            upload_endpoint=upload_endpoint
        )
        s3 = get_s3_client(conn_test)
        try:
            s3.list_buckets()
        except ClientError as ce:
            status_code = ce.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            error_code = ce.response.get('Error', {}).get('Code')
            if status_code != 403 and error_code not in ['AccessDenied', 'AllAccessDisabled']:
                raise ce
        
        # Apply updates
        conn.name = name
        conn.endpoint_url = endpoint_url
        conn.access_key = access_key
        conn.secret_key = secret_key
        conn.region_name = region_name
        conn.upload_endpoint = upload_endpoint

        if old_name != name:
            VideoProgress.query.filter_by(connection_name=old_name).update({VideoProgress.connection_name: name})
            
        db.session.commit()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'success', 'message': 'S3 Connection updated successfully!'})
            
        flash('S3 Connection updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': f'S3 connection test error: {error_msg}'}), 400
        flash(f'S3 connection test error: {error_msg}', 'error')

    return redirect(url_for('dashboard'))


@app.route('/connection/<connection_id>')
def view_connection(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    try:
        s3 = get_s3_client(conn)
        
        raw_buckets = []
        try:
            response = s3.list_buckets()
            raw_buckets = response.get('Buckets', [])
        except ClientError as ce:
            # Catch s3:ListAllMyBuckets Forbidden (AccessDenied) and fall back to mapped/shared buckets
            if ce.response.get('Error', {}).get('Code') in ['AccessDenied', '403'] or 'Forbidden' in str(ce):
                if g.user and g.user.role == 'Admin':
                    mappings = UserBucket.query.filter_by(connection_id=conn.id).all()
                    shared = BucketAccess.query.filter_by(connection_id=conn.id).all()
                elif g.user:
                    mappings = UserBucket.query.filter_by(connection_id=conn.id, user_id=g.user.id).all()
                    shared = BucketAccess.query.filter_by(connection_id=conn.id, user_id=g.user.id).all()
                else:
                    mappings = UserBucket.query.filter_by(connection_id=conn.id).filter(UserBucket.access_type.in_(['public', 'public_view', 'public_edit'])).all()
                    shared = []
                
                mapped_names = set([m.bucket_name for m in mappings] + [s.bucket_name for s in shared])
                raw_buckets = [{'Name': name, 'CreationDate': None} for name in mapped_names]
                flash('Unable to list all buckets (403 Forbidden). Only displaying buckets you are authorized to access.', 'warning')
            else:
                raise ce
        
        # Load bucket maps to determine owner
        mappings = UserBucket.query.filter_by(connection_id=conn.id).all()
        owner_map = {m.bucket_name: m.user for m in mappings}
        
        buckets = []
        for b in raw_buckets:
            name = b['Name']
            owner = owner_map.get(name)
            
            # Use check_bucket_access helper
            if check_bucket_access(g.user, conn, name):
                size = get_bucket_size(s3, name)
                buckets.append({
                    'Name': name,
                    'CreationDate': b.get('CreationDate'),
                    'owner': owner,
                    'Size': size
                })
                
        return render_template('buckets.html', connection=conn, buckets=buckets)
    except Exception as e:
        flash(f'Error connecting to S3 storage: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/connection/<connection_id>/bucket/create', methods=['POST'])
@login_required
def create_bucket(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    bucket_name = request.form.get('bucket_name', '').strip().lower()
    
    if not bucket_name:
        flash('Bucket name cannot be empty.', 'error')
        return redirect(url_for('view_connection', connection_id=connection_id))

    try:
        s3 = get_s3_client(conn)
        kwargs = {'Bucket': bucket_name}
        if conn.region_name and conn.region_name != 'us-east-1' and 'amazonaws.com' in conn.endpoint_url:
            kwargs['CreateBucketConfiguration'] = {'LocationConstraint': conn.region_name}
            
        s3.create_bucket(**kwargs)
        
        # Configure CORS for direct browser upload
        configure_bucket_cors(s3, bucket_name)
        
        # Insert owner map
        mapping = UserBucket(user_id=g.user.id, connection_id=conn.id, bucket_name=bucket_name)
        db.session.add(mapping)
        db.session.commit()
        
        # Log action
        log_action(g.user.id, g.user.id, conn.name, bucket_name, 'CREATE_BUCKET', f"Created bucket '{bucket_name}'")
        
        flash(f'Bucket "{bucket_name}" created successfully.', 'success')
    except Exception as e:
        flash(f'Failed to create bucket: {str(e)}', 'error')

    return redirect(url_for('view_connection', connection_id=connection_id))

@app.route('/connection/<connection_id>/bucket/<bucket_name>/delete', methods=['POST'])
@login_required
def delete_bucket(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    # Only owner or Admin can delete a bucket
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None
    
    if g.user.role != 'Admin' and owner_id != g.user.id:
        flash('Permission Denied. You do not own this bucket.', 'error')
        return redirect(url_for('view_connection', connection_id=connection_id))

    try:
        s3 = get_s3_client(conn)
        
        # Empty the bucket first (handling versioned & unversioned buckets)
        try:
            paginator = s3.get_paginator('list_object_versions')
            for page in paginator.paginate(Bucket=bucket_name):
                delete_keys = []
                for obj in page.get('Versions', []):
                    delete_keys.append({'Key': obj['Key'], 'VersionId': obj['VersionId']})
                for obj in page.get('DeleteMarkers', []):
                    delete_keys.append({'Key': obj['Key'], 'VersionId': obj['VersionId']})
                if delete_keys:
                    s3.delete_objects(Bucket=bucket_name, Delete={'Objects': delete_keys})
        except Exception:
            # Fallback to standard listing if versions are not supported by the provider
            try:
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket_name):
                    delete_keys = []
                    for obj in page.get('Contents', []):
                        delete_keys.append({'Key': obj['Key']})
                    if delete_keys:
                        s3.delete_objects(Bucket=bucket_name, Delete={'Objects': delete_keys})
            except Exception:
                pass

        s3.delete_bucket(Bucket=bucket_name)
        
        # Clean up related VideoProgress records
        VideoProgress.query.filter_by(
            connection_name=conn.name,
            bucket_name=bucket_name
        ).delete()
        
        # Delete mapping and any shared access grants
        if mapping:
            db.session.delete(mapping)
        BucketAccess.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).delete()
            
        db.session.commit()
            
        # Log action
        details = "Deleted bucket owned by user" if g.user.id == owner_id else f"Admin {g.user.name} deleted user's bucket"
        log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_BUCKET', details)
        
        flash(f'Bucket "{bucket_name}" deleted successfully.', 'success')
    except Exception as e:
        flash(f'Failed to delete bucket: {str(e)}', 'error')

    return redirect(url_for('view_connection', connection_id=connection_id))

@app.route('/connection/<connection_id>/bucket/<bucket_name>/browse')
def browse_bucket(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    prefix = request.args.get('prefix', '')
    sort_by = request.args.get('sort', 'name')
    direction = request.args.get('direction', 'asc')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    # Require login if the bucket is not public
    if not is_public and g.user is None:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('login'))
        
    # Ownership or shared access verification
    if not check_bucket_access(g.user, conn, bucket_name):
        flash('Permission Denied. You do not have access to this bucket.', 'error')
        return redirect(url_for('view_connection', connection_id=connection_id))
        
    owner_id = mapping.user_id if mapping else None

    try:
        s3 = get_s3_client(conn)
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3_public = get_s3_client(conn, endpoint_url=public_endpoint)
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
        
        folders = []
        files = []
        
        progress_map = {}
        if g.user:
            progresses = VideoProgress.query.filter_by(
                user_id=g.user.id,
                connection_name=conn.name,
                bucket_name=bucket_name
            ).all()
            progress_map = {p.file_key: p for p in progresses}
        
        # Fetch file creators
        uploaded_files = UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name
        ).all()
        creator_map = {uf.file_key: uf.user.name for uf in uploaded_files}
        
        for page in pages:
            for cp in page.get('CommonPrefixes', []):
                folders.append(cp.get('Prefix'))
                
            for obj in page.get('Contents', []):
                if obj.get('Key') == prefix:
                     continue
                key = obj.get('Key')
                prog = progress_map.get(key)
                
                ext = key.split('.')[-1].lower() if '.' in key else ''
                is_previewable = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
                
                file_cat = 'other'
                if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']:
                    file_cat = 'image'
                elif ext in ['mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']:
                    file_cat = 'video'
                
                url = None
                if is_previewable:
                    try:
                        url = fix_s3_url(s3_public.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket_name, 'Key': key},
                            ExpiresIn=3600
                        ))
                    except Exception:
                        pass

                files.append({
                    'key': key,
                    'name': key.split('/')[-1],
                    'size': obj.get('Size'),
                    'created_by': creator_map.get(key, 'Unknown'),
                    'last_modified': obj.get('LastModified'),
                    'presigned_url': url,
                    'progress': {
                        'seconds': prog.seconds_watched,
                        'duration': prog.duration,
                        'pct': round(prog.seconds_watched / prog.duration * 100, 1) if (prog and prog.duration > 0) else 0
                    } if prog else None
                })
                
        # Sort folders & files
        reverse_sort = (direction == 'desc')
        folders.sort(key=lambda x: x.lower(), reverse=reverse_sort)
        
        if sort_by == 'size':
            files.sort(key=lambda x: x.get('size') or 0, reverse=reverse_sort)
        elif sort_by == 'last_modified':
            from datetime import datetime, timezone
            min_dt = datetime.min.replace(tzinfo=timezone.utc)
            files.sort(key=lambda x: x.get('last_modified') or min_dt, reverse=reverse_sort)
        else: # default: name
            files.sort(key=lambda x: x.get('name', '').lower(), reverse=reverse_sort)
            
        can_edit = check_bucket_edit_access(g.user, conn, bucket_name)
                
        return render_template(
            'browser.html',
            connection=conn,
            bucket_name=bucket_name,
            prefix=prefix,
            folders=folders,
            files=files,
            bucket_owner_id=owner_id,
            can_edit=can_edit,
            sort_by=sort_by,
            direction=direction
        )
    except Exception as e:
        flash(f'Failed to browse bucket contents: {str(e)}', 'error')
        return redirect(url_for('view_connection', connection_id=connection_id))

@app.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/initiate', methods=['POST'])
@login_required
def multipart_initiate(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    filename = data.get('filename')
    filesize = data.get('filesize')
    filetype = data.get('filetype') or 'application/octet-stream'
    prefix = data.get('prefix', '')

    if not filename or filesize is None:
        return jsonify({'status': 'error', 'message': 'Missing filename or filesize.'}), 400

    if filename.lower().endswith(('.exe', '.dll', '.msi', '.bat', '.sh', '.cmd', '.com', '.lnk', '.sys')):
        return jsonify({'status': 'error', 'message': 'Upload of executable files (.exe, .dll, .msi, etc.) is blocked for security reasons.'}), 400

    try:
        quota_owner_id = owner_id if owner_id else g.user.id
        quota_owner = db.session.get(User, quota_owner_id)
        
        if quota_owner.role != 'Admin':
            used = get_user_storage_used(quota_owner)
            limit = quota_owner.quota_limit or 2147483648
            
            if used + filesize > limit:
                return jsonify({
                    'status': 'error', 
                    'message': f'Storage quota exceeded. Available: {round((limit - used)/1048576, 1)}MB.'
                }), 400

        parts = [secure_filename(p) for p in filename.split('/') if p]
        filename_secured = '/'.join(parts)
        key = prefix + filename_secured
        
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        
        response = s3.create_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            ContentType=filetype
        )
        
        return jsonify({
            'status': 'success',
            'uploadId': response['UploadId'],
            'key': key
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/presign-part', methods=['POST'])
@login_required
def multipart_presign_part(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    data = request.get_json() or {}
    upload_id = data.get('uploadId')
    key = data.get('key')
    part_number = data.get('partNumber')

    if not all([upload_id, key, part_number]):
        return jsonify({'status': 'error', 'message': 'Missing uploadId, key, or partNumber.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        
        presigned_url = s3.generate_presigned_url(
            ClientMethod='upload_part',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                'UploadId': upload_id,
                'PartNumber': int(part_number)
            },
            ExpiresIn=3600
        )
        
        return jsonify({
            'status': 'success',
            'url': fix_s3_url(presigned_url)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/complete', methods=['POST'])
@login_required
def multipart_complete(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    upload_id = data.get('uploadId')
    key = data.get('key')
    parts = data.get('parts')

    if not all([upload_id, key, parts]):
        return jsonify({'status': 'error', 'message': 'Missing uploadId, key, or parts.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        
        # S3 expects Parts to be sorted by PartNumber
        sorted_parts = sorted(parts, key=lambda x: x['PartNumber'])
        
        s3.complete_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': sorted_parts}
        )
        
        # Retrieve actual size
        actual_size = 0
        try:
            response = s3.head_object(Bucket=bucket_name, Key=key)
            actual_size = response.get('ContentLength', 0)
        except Exception as e:
            print(f"Warning: Failed to get object size: {e}")
            
        filename = key.split('/')[-1]
        quota_owner_id = owner_id if owner_id else g.user.id
        
        log_action(
            g.user.id, 
            quota_owner_id, 
            conn.name, 
            bucket_name, 
            'UPLOAD_FILE', 
            f"Uploaded file '{filename}' ({actual_size} bytes) via Multipart to S3"
        )
        
        # Save uploader metadata
        existing_upload = UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name,
            file_key=key
        ).first()
        if existing_upload:
            existing_upload.user_id = g.user.id
            existing_upload.created_at = datetime.utcnow()
        else:
            uploaded_file = UploadedFile(
                connection_id=conn.id,
                bucket_name=bucket_name,
                file_key=key,
                user_id=g.user.id
            )
            db.session.add(uploaded_file)
        db.session.commit()
        
        # Trigger background preview generation
        ext = key.split('.')[-1].lower() if '.' in key else ''
        is_previewable = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
        if is_previewable:
            file_cat = 'image' if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'] else 'video'
            from flask import current_app
            threading.Thread(
                target=generate_and_cache_preview_task,
                args=(current_app._get_current_object(), conn.id, bucket_name, key, file_cat)
            ).start()
        
        return jsonify({'status': 'success', 'size': actual_size})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/abort', methods=['POST'])
@login_required
def multipart_abort(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    data = request.get_json() or {}
    upload_id = data.get('uploadId')
    key = data.get('key')

    if not all([upload_id, key]):
        return jsonify({'status': 'error', 'message': 'Missing uploadId or key.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        
        s3.abort_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            UploadId=upload_id
        )
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/presign-upload', methods=['POST'])
@login_required
def presign_upload(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    filename = data.get('filename')
    filesize = data.get('filesize')
    filetype = data.get('filetype') or 'application/octet-stream'
    prefix = data.get('prefix', '')

    if not filename or filesize is None:
        return jsonify({'status': 'error', 'message': 'Missing filename or filesize.'}), 400

    if filename.lower().endswith(('.exe', '.dll', '.msi', '.bat', '.sh', '.cmd', '.com', '.lnk', '.sys')):
        return jsonify({'status': 'error', 'message': 'Upload of executable files (.exe, .dll, .msi, etc.) is blocked for security reasons.'}), 400

    try:
        quota_owner_id = owner_id if owner_id else g.user.id
        quota_owner = db.session.get(User, quota_owner_id)
        
        if quota_owner.role != 'Admin':
            used = get_user_storage_used(quota_owner)
            limit = quota_owner.quota_limit or 2147483648
            
            if used + filesize > limit:
                return jsonify({
                    'status': 'error', 
                    'message': f'Storage quota exceeded. Available: {round((limit - used)/1048576, 1)}MB.'
                }), 400

        filename_secured = secure_filename(filename)
        key = prefix + filename_secured
        
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        presigned = s3.generate_presigned_post(
            Bucket=bucket_name,
            Key=key,
            Fields={'Content-Type': filetype},
            Conditions=[
                ['content-length-range', 0, filesize],
                {'Content-Type': filetype}
            ],
            ExpiresIn=3600
        )
        
        return jsonify({
            'status': 'success',
            'url': fix_s3_url(presigned['url']),
            'fields': presigned['fields'],
            'key': key
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/confirm-upload', methods=['POST'])
@login_required
def confirm_upload(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    key = data.get('key')

    if not key:
        return jsonify({'status': 'error', 'message': 'Missing key.'}), 400

    try:
        s3 = get_s3_client(conn)
        actual_size = 0
        try:
            response = s3.head_object(Bucket=bucket_name, Key=key)
            actual_size = response.get('ContentLength', 0)
        except Exception as e:
            print(f"Warning: Failed to get object size via HeadObject: {e}")
            
        filename = key.split('/')[-1]
        quota_owner_id = owner_id if owner_id else g.user.id
        
        log_action(
            g.user.id, 
            quota_owner_id, 
            conn.name, 
            bucket_name, 
            'UPLOAD_FILE', 
            f"Uploaded file '{filename}' ({actual_size} bytes) directly to S3"
        )
        
        # Save or update file uploader/creator metadata
        existing_upload = UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name,
            file_key=key
        ).first()
        if existing_upload:
            existing_upload.user_id = g.user.id
            existing_upload.created_at = datetime.utcnow()
        else:
            uploaded_file = UploadedFile(
                connection_id=conn.id,
                bucket_name=bucket_name,
                file_key=key,
                user_id=g.user.id
            )
            db.session.add(uploaded_file)
        db.session.commit()
        
        # Trigger background preview generation
        ext = key.split('.')[-1].lower() if '.' in key else ''
        is_previewable = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
        if is_previewable:
            file_cat = 'image' if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'] else 'video'
            from flask import current_app
            threading.Thread(
                target=generate_and_cache_preview_task,
                args=(current_app._get_current_object(), conn.id, bucket_name, key, file_cat)
            ).start()
        
        return jsonify({'status': 'success', 'size': actual_size})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/create-folder', methods=['POST'])
@login_required
def create_folder(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
    data = request.get_json() or {}
    folder_name = data.get('folder_name', '').strip()
    prefix = data.get('prefix', '')
    if not folder_name:
        return jsonify({'status': 'error', 'message': 'Folder name cannot be empty.'}), 400
    from werkzeug.utils import secure_filename
    secured_name = secure_filename(folder_name)
    if not secured_name:
        return jsonify({'status': 'error', 'message': 'Invalid folder name.'}), 400
    key = prefix + secured_name + '/'
    try:
        s3 = get_s3_client(conn)
        s3.put_object(Bucket=bucket_name, Key=key, Body=b'')
        log_action(g.user.id, None, conn.name, bucket_name, 'CREATE_FOLDER', f"Created folder '{key}'")
        return jsonify({'status': 'success', 'message': 'Folder created successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/save-text', methods=['POST'])
@login_required
def save_text_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    key = data.get('key')
    content = data.get('content')

    if not key or content is None:
        return jsonify({'status': 'error', 'message': 'Missing key or content.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=endpoint_url)
        
        filename = key.split('/')[-1]
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        content_type = 'text/plain; charset=utf-8'
        if ext == 'json':
            content_type = 'application/json; charset=utf-8'
        elif ext == 'csv':
            content_type = 'text/csv; charset=utf-8'
        elif ext == 'xml':
            content_type = 'application/xml; charset=utf-8'
        elif ext in ['yaml', 'yml']:
            content_type = 'text/yaml; charset=utf-8'
        elif ext == 'md':
            content_type = 'text/markdown; charset=utf-8'
        elif ext == 'html':
            content_type = 'text/html; charset=utf-8'
        elif ext == 'js':
            content_type = 'application/javascript; charset=utf-8'
        elif ext == 'css':
            content_type = 'text/css; charset=utf-8'

        body_bytes = content.encode('utf-8')
        actual_size = len(body_bytes)

        quota_owner_id = owner_id if owner_id else g.user.id
        quota_owner = db.session.get(User, quota_owner_id)
        
        if quota_owner.role != 'Admin':
            old_size = 0
            try:
                old_meta = s3.head_object(Bucket=bucket_name, Key=key)
                old_size = old_meta.get('ContentLength', 0)
            except Exception:
                pass
            
            size_diff = actual_size - old_size
            if size_diff > 0:
                used = get_user_storage_used(quota_owner)
                limit = quota_owner.quota_limit or 2147483648
                if used + size_diff > limit:
                    return jsonify({
                        'status': 'error', 
                        'message': f'Storage quota exceeded. Available: {round((limit - used)/1048576, 1)}MB.'
                    }), 400

        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=body_bytes,
            ContentType=content_type
        )
        
        log_action(
            g.user.id, 
            quota_owner_id, 
            conn.name, 
            bucket_name, 
            'EDIT_FILE', 
            f"Edited text file '{filename}' ({actual_size} bytes) directly on the web"
        )
        
        existing_upload = UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name,
            file_key=key
        ).first()
        if existing_upload:
            existing_upload.user_id = g.user.id
            existing_upload.created_at = datetime.utcnow()
        else:
            uploaded_file = UploadedFile(
                connection_id=conn.id,
                bucket_name=bucket_name,
                file_key=key,
                user_id=g.user.id
            )
            db.session.add(uploaded_file)
        db.session.commit()
        
        return jsonify({'status': 'success', 'size': actual_size})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/rename', methods=['POST'])
@login_required
def rename_object(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    data = request.get_json() or {}
    old_key = data.get('old_key')
    new_name = data.get('new_name')
    
    if not old_key or not new_name:
        return jsonify({'status': 'error', 'message': 'Missing parameters.'}), 400
        
    is_dir = old_key.endswith('/')
    if not is_dir:
        import os
        old_filename = old_key.split('/')[-1]
        _, old_ext = os.path.splitext(old_filename)
        new_name_base, _ = os.path.splitext(new_name)
        secured_base = secure_filename(new_name_base)
        if not secured_base:
            return jsonify({'status': 'error', 'message': 'Invalid new name.'}), 400
        new_name = secured_base + old_ext
    else:
        new_name = secure_filename(new_name)
        if not new_name:
            return jsonify({'status': 'error', 'message': 'Invalid new name.'}), 400
            
    try:
        s3 = get_s3_client(conn)
        
        if is_dir:
            parts = old_key.rstrip('/').split('/')
            parent_path = '/'.join(parts[:-1]) + '/' if len(parts) > 1 else ''
            new_key = parent_path + new_name + '/'
            
            paginator = s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name, Prefix=old_key)
            
            objects_to_delete = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        source_key = obj['Key']
                        dest_key = new_key + source_key[len(old_key):]
                        
                        s3.copy_object(
                            Bucket=bucket_name,
                            CopySource={'Bucket': bucket_name, 'Key': source_key},
                            Key=dest_key
                        )
                        objects_to_delete.append({'Key': source_key})
            
            if objects_to_delete:
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i+1000]
                    s3.delete_objects(
                        Bucket=bucket_name,
                        Delete={'Objects': batch}
                    )
                    
            # Update DB records for folder rename
            uploaded_files = UploadedFile.query.filter(
                UploadedFile.connection_id == conn.id,
                UploadedFile.bucket_name == bucket_name,
                UploadedFile.file_key.startswith(old_key)
            ).all()
            for uf in uploaded_files:
                uf.file_key = new_key + uf.file_key[len(old_key):]
                
            progress_records = VideoProgress.query.filter(
                VideoProgress.connection_name == conn.name,
                VideoProgress.bucket_name == bucket_name,
                VideoProgress.file_key.startswith(old_key)
            ).all()
            for pr in progress_records:
                pr.file_key = new_key + pr.file_key[len(old_key):]
                
            # Update Video Notes for folder rename
            note_records = VideoNote.query.filter(
                VideoNote.connection_name == conn.name,
                VideoNote.bucket_name == bucket_name,
                VideoNote.file_key.startswith(old_key)
            ).all()
            for nr in note_records:
                nr.file_key = new_key + nr.file_key[len(old_key):]
                
            db.session.commit()
                    
            log_action(g.user.id, None, conn.name, bucket_name, 'RENAME_FOLDER', f"Renamed folder {old_key} to {new_key}")
        else:
            parts = old_key.split('/')
            parent_path = '/'.join(parts[:-1]) + '/' if len(parts) > 1 else ''
            new_key = parent_path + new_name
            
            s3.copy_object(
                Bucket=bucket_name,
                CopySource={'Bucket': bucket_name, 'Key': old_key},
                Key=new_key
            )
            s3.delete_object(Bucket=bucket_name, Key=old_key)
            
            # Update DB records for file rename
            uploaded_file = UploadedFile.query.filter_by(
                connection_id=conn.id,
                bucket_name=bucket_name,
                file_key=old_key
            ).first()
            if uploaded_file:
                uploaded_file.file_key = new_key
                
            progress_record = VideoProgress.query.filter_by(
                connection_name=conn.name,
                bucket_name=bucket_name,
                file_key=old_key
            ).first()
            if progress_record:
                progress_record.file_key = new_key
                progress_record.file_name = new_name
                
            # Update Video Notes for file rename
            note_records = VideoNote.query.filter_by(
                connection_name=conn.name,
                bucket_name=bucket_name,
                file_key=old_key
            ).all()
            for nr in note_records:
                nr.file_key = new_key
                
            db.session.commit()
            
            log_action(g.user.id, None, conn.name, bucket_name, 'RENAME_FILE', f"Renamed file {old_key} to {new_key}")
            
        return jsonify({'status': 'success', 'message': 'Renamed successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/delete-object', methods=['POST'])
@login_required
def delete_object(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.form.get('key')
    prefix = request.form.get('prefix', '')
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        flash('Permission Denied.', 'error')
        return redirect(url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))
        
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    if not key:
        flash('No object key specified.', 'error')
        return redirect(url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))

    try:
        s3 = get_s3_client(conn)
        s3.delete_object(Bucket=bucket_name, Key=key)
        
        # Clean up related VideoProgress records
        VideoProgress.query.filter_by(
            connection_name=conn.name,
            bucket_name=bucket_name,
            file_key=key
        ).delete()
        
        # Clean up uploader metadata
        UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name,
            file_key=key
        ).delete()
        
        db.session.commit()
        
        # Log action
        details = f"Deleted file '{key.split('/')[-1]}'" if g.user.id == owner_id else f"Admin {g.user.name} deleted file '{key.split('/')[-1]}'"
        log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_FILE', details)
        
        flash('File deleted successfully.', 'success')
    except Exception as e:
        flash(f'Failed to delete file: {str(e)}', 'error')

    return redirect(url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))

@app.route('/connection/<connection_id>/bucket/<bucket_name>/delete-objects-bulk', methods=['POST'])
@login_required
def delete_objects_bulk(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    keys = data.get('keys', [])
    
    if not keys:
        return jsonify({'status': 'error', 'message': 'No object keys specified.'}), 400

    try:
        s3 = get_s3_client(conn)
        objects_to_delete = [{'Key': key} for key in keys]
        
        for i in range(0, len(objects_to_delete), 1000):
            chunk = objects_to_delete[i:i+1000]
            s3.delete_objects(Bucket=bucket_name, Delete={'Objects': chunk})
            
        VideoProgress.query.filter_by(
            connection_name=conn.name,
            bucket_name=bucket_name
        ).filter(VideoProgress.file_key.in_(keys)).delete(synchronize_session=False)
        
        UploadedFile.query.filter_by(
            connection_id=conn.id,
            bucket_name=bucket_name
        ).filter(UploadedFile.file_key.in_(keys)).delete(synchronize_session=False)
        
        db.session.commit()
        
        count = len(keys)
        details = f"Bulk deleted {count} files" if g.user.id == owner_id else f"Admin {g.user.name} bulk deleted {count} files"
        log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_FILE', details)
        
        return jsonify({'status': 'success', 'message': f'Successfully deleted {count} files.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to delete files: {str(e)}'}), 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/viewer')
def view_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('login'))
        
    if not check_bucket_access(g.user, conn, bucket_name):
        flash('Permission Denied.', 'error')
        return redirect(url_for('view_connection', connection_id=connection_id))
        
    owner_id = mapping.user_id if mapping else None

    if not key:
        flash('No file key specified for viewing.', 'error')
        return redirect(url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name))
        
    try:
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=public_endpoint)
        presigned_url = fix_s3_url(s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600
        ))
        
        filename = key.split('/')[-1]
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        
        video_exts = ['mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
        audio_exts = ['mp3', 'wav', 'ogg', 'aac', 'flac']
        pdf_exts = ['pdf']
        ppt_exts = ['ppt', 'pptx']
        docx_exts = ['doc', 'docx']
        image_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']
        text_exts = ['txt', 'json', 'csv', 'xml', 'log', 'ini', 'cfg', 'yaml', 'yml', 'md', 'html', 'js', 'css']
        
        file_type = 'unknown'
        if ext in video_exts:
            file_type = 'video'
        elif ext in audio_exts:
            file_type = 'audio'
        elif ext in pdf_exts:
            file_type = 'pdf'
        elif ext in ppt_exts:
            file_type = 'powerpoint'
        elif ext in docx_exts:
            file_type = 'docx'
        elif ext in image_exts:
            file_type = 'image'
        elif ext in text_exts:
            file_type = 'text'
 
        is_local_endpoint = False
        if conn.endpoint_url:
            parsed_url = urllib.parse.urlparse(conn.endpoint_url)
            hostname = parsed_url.hostname or ''
            if hostname in ['localhost', '127.0.0.1'] or hostname.startswith('192.168.') or hostname.startswith('10.'):
                is_local_endpoint = True
            
        resume_seconds = 0
        if g.user:
            progress = VideoProgress.query.filter_by(
                user_id=g.user.id,
                connection_name=conn.name,
                bucket_name=bucket_name,
                file_key=key
            ).first()
            resume_seconds = progress.seconds_watched if (progress and progress.seconds_watched > 0) else 0
 
        is_https_site = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
        is_http_s3 = conn.endpoint_url and conn.endpoint_url.startswith('http://')
        use_proxy = is_https_site and is_http_s3 and file_type in ['pdf', 'video', 'audio', 'image', 'text']
        
        if use_proxy:
            file_url = url_for('proxy_s3_file', connection_id=connection_id, bucket_name=bucket_name, key=key)
        else:
            file_url = presigned_url

        can_edit = check_bucket_edit_access(g.user, conn, bucket_name)

        return render_template(
            'viewer.html',
            connection=conn,
            bucket_name=bucket_name,
            key=key,
            filename=filename,
            file_type=file_type,
            presigned_url=file_url,
            is_local_endpoint=is_local_endpoint,
            resume_seconds=resume_seconds,
            can_edit=can_edit
        )
    except Exception as e:
        flash(f'Could not view file: {str(e)}', 'error')
        return redirect(url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name))

from flask import Response, stream_with_context

@app.route('/connection/<connection_id>/bucket/<bucket_name>/proxy-file')
def proxy_s3_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return "Authentication required", 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return "Permission Denied", 403
        
    if not key:
        return "Missing file key", 400
        
    try:
        s3 = get_s3_client(conn)
        kwargs = {'Bucket': bucket_name, 'Key': key}
        
        range_header = request.headers.get('Range')
        if range_header:
            kwargs['Range'] = range_header
            
        s3_object = s3.get_object(**kwargs)
        status_code = 206 if range_header else 200
        
        headers = {
            'Content-Type': s3_object.get('ContentType', 'application/octet-stream'),
            'Content-Length': str(s3_object.get('ContentLength', '')),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(key.split("/")[-1])}"'
        }
        
        if 'ContentRange' in s3_object:
            headers['Content-Range'] = s3_object['ContentRange']
            
        def generate():
            body = s3_object['Body']
            for chunk in body.iter_chunks(chunk_size=1024*64):
                yield chunk
                
        return Response(stream_with_context(generate()), status=status_code, headers=headers)
    except Exception as e:
        return f"Error proxying file: {str(e)}", 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/office-to-pdf')
def office_to_pdf(connection_id, bucket_name):
    import os
    import subprocess
    import tempfile
    import shutil

    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return "Authentication required", 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return "Permission Denied", 403
        
    if not key:
        return "Missing file key", 400

    temp_dir = tempfile.mkdtemp()
    try:
        s3 = get_s3_client(conn)
        filename = key.split('/')[-1]
        input_path = os.path.join(temp_dir, filename)
        
        # Download file from S3 to temporary directory
        s3.download_file(bucket_name, key, input_path)
        
        # Convert to pdf using libreoffice
        result = subprocess.run(
            ['soffice', '--headless', '--convert-to', 'pdf', '--outdir', temp_dir, input_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return f"LibreOffice conversion failed: {result.stderr}", 500
            
        base_name = os.path.splitext(filename)[0]
        pdf_filename = f"{base_name}.pdf"
        pdf_path = os.path.join(temp_dir, pdf_filename)
        
        if not os.path.exists(pdf_path):
            return "Converted PDF not found", 500
            
        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()
            
        headers = {
            'Content-Type': 'application/pdf',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(pdf_filename)}"'
        }
        return Response(pdf_data, status=200, headers=headers)
        
    except subprocess.TimeoutExpired:
        return "Conversion timed out", 504
    except Exception as e:
        return f"Error converting file: {str(e)}", 500
    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

@app.route('/connection/<connection_id>/bucket/<bucket_name>/flv-to-mp4')
def flv_to_mp4(connection_id, bucket_name):
    import os
    import subprocess
    import tempfile
    import shutil
    import urllib.parse
    from flask import Response, stream_with_context, request, g

    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return "Authentication required", 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return "Permission Denied", 403
        
    if not key:
        return "Missing file key", 400

    try:
        s3 = get_s3_client(conn)
        temp_dir = tempfile.mkdtemp()
        filename = key.split('/')[-1]
        input_path = os.path.join(temp_dir, filename)
        
        # Download from S3 to local temp path
        s3.download_file(bucket_name, key, input_path)
        
        # Transcode to fragmented MP4 (compatible with html5 video tag streaming)
        cmd = [
            'ffmpeg', '-i', input_path,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '128k',
            '-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov',
            'pipe:1'
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=10**6
        )
        
        def generate():
            try:
                while True:
                    data = process.stdout.read(4096 * 16)
                    if not data:
                        break
                    yield data
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    pass
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

        headers = {
            'Content-Type': 'video/mp4',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(filename.replace(".flv", ".mp4"))}"'
        }
        return Response(stream_with_context(generate()), status=200, headers=headers)
        
    except Exception as e:
        return f"Error converting video: {str(e)}", 500


# --- Video Playback Tracking Routes ---

@app.route('/video/progress', methods=['POST'])
@login_required
def update_video_progress():
    data = request.get_json() or {}
    connection_name = data.get('connection_name')
    bucket_name = data.get('bucket_name')
    file_key = data.get('file_key')
    file_name = data.get('file_name')
    seconds_watched = data.get('seconds_watched', 0.0)
    duration = data.get('duration', 0.0)

    if not all([connection_name, bucket_name, file_key, file_name]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400

    progress = VideoProgress.query.filter_by(
        user_id=g.user.id,
        connection_name=connection_name,
        bucket_name=bucket_name,
        file_key=file_key
    ).first()

    if not progress:
        progress = VideoProgress(
            user_id=g.user.id,
            connection_name=connection_name,
            bucket_name=bucket_name,
            file_key=file_key,
            file_name=file_name
        )
        db.session.add(progress)

    progress.seconds_watched = seconds_watched
    progress.duration = duration
    progress.last_watched_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/progress')
@login_required
def list_progress():
    progress_records = VideoProgress.query.filter_by(user_id=g.user.id).all()
    connections = S3Connection.query.all()
    conn_map = {c.name: c.connection_id for c in connections}

    grouped_progress = {}
    for record in progress_records:
        record.connection_id = conn_map.get(record.connection_name)
        bucket = record.bucket_name
        if bucket not in grouped_progress:
            grouped_progress[bucket] = []
        grouped_progress[bucket].append(record)

    for bucket in grouped_progress:
        grouped_progress[bucket].sort(key=lambda x: x.file_name.lower())

    return render_template('progress.html', grouped_progress=grouped_progress)

@app.route('/progress/delete-item/<int:progress_id>', methods=['POST'])
@login_required
def delete_progress_item(progress_id):
    progress = db.get_or_404(VideoProgress, progress_id)
    if progress.user_id != g.user.id:
        flash('Permission Denied.', 'error')
        return redirect(url_for('list_progress'))
        
    db.session.delete(progress)
    db.session.commit()
    flash(f"Deleted progress for file: {progress.file_name}", 'success')
    return redirect(url_for('list_progress'))

@app.route('/progress/delete-bucket/<bucket_name>', methods=['POST'])
@login_required
def delete_progress_bucket(bucket_name):
    VideoProgress.query.filter_by(user_id=g.user.id, bucket_name=bucket_name).delete()
    db.session.commit()
    flash(f"Deleted all progress records for bucket: {bucket_name}", 'success')
    return redirect(url_for('list_progress'))

# --- Admin Management & Audit Logs Routes ---

@app.route('/admin/users')
@admin_required
def manage_users():
    users = User.query.order_by(User.created_at.desc()).all()
    
    # Calculate storage stats for each user
    user_stats = []
    for u in users:
        used = get_user_storage_used(u)
        user_stats.append({
            'user': u,
            'storage_used': used,
            'quota_limit': u.quota_limit or 2147483648
        })
    return render_template('users.html', user_stats=user_stats)

@app.route('/admin/user/<int:user_id>/quota', methods=['POST'])
@admin_required
def update_user_quota(user_id):
    user = db.get_or_404(User, user_id)
    quota_gb = request.form.get('quota_gb', type=float)
    
    if quota_gb is None or quota_gb <= 0:
        flash('Invalid quota value.', 'error')
        return redirect(url_for('manage_users'))
        
    user.quota_limit = int(quota_gb * 1024 * 1024 * 1024)
    db.session.commit()
    flash(f"Quota for {user.name} updated to {quota_gb} GB.", 'success')
    return redirect(url_for('manage_users'))

@app.route('/admin/functions')
@admin_required
def admin_functions():
    return render_template('admin_functions.html')

@app.route('/connection/<connection_id>/bucket/<bucket_name>/hls/playlist.m3u8')
def flv_hls_playlist(connection_id, bucket_name):
    import subprocess
    from flask import Response, request, g, url_for
    import urllib.parse
    
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return "Authentication required", 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return "Permission Denied", 403
        
    if not key:
        return "Missing file key", 400
        
    try:
        s3 = get_s3_client(conn)
        # Generate presigned URL for ffprobe to check duration
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600
        )
        
        # Get duration using ffprobe
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            presigned_url
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if result.returncode != 0:
            raise Exception(f"ffprobe failed: {result.stderr}")
            
        duration = float(result.stdout.strip())
        
        # Segment duration: 6 seconds is standard and works well for quick seeking
        seg_dur = 6.0
        num_segments = int(duration // seg_dur)
        remainder = duration % seg_dur
        
        playlist_lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{int(seg_dur + 1)}",
            "#EXT-X-MEDIA-SEQUENCE:0"
        ]
        
        for i in range(num_segments):
            start = i * seg_dur
            seg_url = url_for(
                'flv_hls_segment',
                connection_id=connection_id,
                bucket_name=bucket_name,
                key=key,
                start=start,
                duration=seg_dur
            )
            playlist_lines.append(f"#EXTINF:{seg_dur:.2f},")
            playlist_lines.append(seg_url)
            
        if remainder > 0.1:
            start = num_segments * seg_dur
            seg_url = url_for(
                'flv_hls_segment',
                connection_id=connection_id,
                bucket_name=bucket_name,
                key=key,
                start=start,
                duration=remainder
            )
            playlist_lines.append(f"#EXTINF:{remainder:.2f},")
            playlist_lines.append(seg_url)
            
        playlist_lines.append("#EXT-X-ENDLIST")
        
        playlist_content = "\n".join(playlist_lines)
        return Response(playlist_content, mimetype='application/x-mpegURL')
        
    except Exception as e:
        app.logger.error(f"Error generating HLS playlist for {key}: {str(e)}")
        return f"Error generating HLS playlist: {str(e)}", 500

@app.route('/connection/<connection_id>/bucket/<bucket_name>/hls/segment.ts')
def flv_hls_segment(connection_id, bucket_name):
    import subprocess
    from flask import Response, request, g, stream_with_context
    
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    start = request.args.get('start', type=float)
    duration = request.args.get('duration', type=float)
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return "Authentication required", 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return "Permission Denied", 403
        
    if not key or start is None or duration is None:
        return "Missing parameters", 400
        
    try:
        s3 = get_s3_client(conn)
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600
        )
        
        # Start ffmpeg seeking to 'start' and taking 'duration'
        # -output_ts_offset shifts the timestamps to match the segment start time in the HLS timeline
        cmd = [
            'ffmpeg', '-ss', f'{start:.2f}', '-t', f'{duration:.2f}',
            '-i', presigned_url,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
            '-c:a', 'aac', '-b:a', '128k',
            '-output_ts_offset', f'{start:.2f}',
            '-muxdelay', '0',
            '-f', 'mpegts', 'pipe:1'
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=10**6
        )
        
        def generate():
            try:
                while True:
                    data = process.stdout.read(4096 * 16)
                    if not data:
                        break
                    yield data
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except Exception:
                    pass
                    
        return Response(stream_with_context(generate()), mimetype='video/MP2T')
        
    except Exception as e:
        app.logger.error(f"Error streaming HLS segment {start} for {key}: {str(e)}")
        return f"Error streaming HLS segment: {str(e)}", 500

@app.route('/admin/bucket-access')
@admin_required
def bucket_access_list():
    access_list = BucketAccess.query.order_by(BucketAccess.created_at.desc()).all()
    users = User.query.filter(User.role != 'Admin').order_by(User.name).all()
    connections = S3Connection.query.order_by(S3Connection.name).all()
    return render_template('bucket_access.html', access_list=access_list, users=users, connections=connections)

@app.route('/admin/bucket-access/grant', methods=['POST'])
@admin_required
def bucket_access_grant():
    user_id = request.form.get('user_id', type=int)
    connection_id = request.form.get('connection_id', type=int)
    bucket_name = request.form.get('bucket_name', '').strip().lower()

    if not all([user_id, connection_id, bucket_name]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('bucket_access_list'))

    user = db.session.get(User, user_id)
    conn = db.session.get(S3Connection, connection_id)

    if not user or not conn:
        flash('User or connection does not exist.', 'error')
        return redirect(url_for('bucket_access_list'))

    # Check if grant already exists
    existing = BucketAccess.query.filter_by(user_id=user_id, connection_id=connection_id, bucket_name=bucket_name).first()
    if existing:
        flash('User already has access to this bucket.', 'warning')
        return redirect(url_for('bucket_access_list'))

    # Grant access
    access = BucketAccess(user_id=user_id, connection_id=connection_id, bucket_name=bucket_name)
    db.session.add(access)
    db.session.commit()

    log_action(
        g.user.id,
        user_id,
        conn.name,
        bucket_name,
        'GRANT_ACCESS',
        f"Admin {g.user.name} granted access to bucket '{bucket_name}' for user {user.name}"
    )

    flash(f"Granted access to bucket '{bucket_name}' for {user.name} successfully.", 'success')
    return redirect(url_for('bucket_access_list'))

@app.route('/admin/bucket-access/<int:access_id>/revoke', methods=['POST'])
@admin_required
def bucket_access_revoke(access_id):
    access = db.get_or_404(BucketAccess, access_id)
    user_id = access.user_id
    user_name = access.user.name
    conn_name = access.connection.name
    bucket_name = access.bucket_name

    # Delete video progress for this user, connection, and bucket
    VideoProgress.query.filter_by(
        user_id=user_id,
        connection_name=conn_name,
        bucket_name=bucket_name
    ).delete()

    db.session.delete(access)
    db.session.commit()

    log_action(
        g.user.id,
        user_id,
        conn_name,
        bucket_name,
        'REVOKE_ACCESS',
        f"Admin {g.user.name} revoked access to bucket '{bucket_name}' for user {user_name}"
    )

    flash(f"Revoked access to bucket '{bucket_name}' of {user_name} successfully.", 'success')
    return redirect(url_for('bucket_access_list'))

@app.route('/logs')
@login_required
def view_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    if g.user.role == 'Admin':
        query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    else:
        query = AuditLog.query.filter(
            (AuditLog.user_id == g.user.id) | (AuditLog.target_user_id == g.user.id)
        ).order_by(AuditLog.timestamp.desc())
        
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    
    return render_template('logs.html', logs=logs, pagination=pagination)

@app.route('/admin/system-logs')
@admin_required
def view_system_logs():
    lines_to_read = request.args.get('limit', 200, type=int)
    log_level = request.args.get('level', 'ALL').upper()
    
    log_lines = []
    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
                
            for line in all_lines:
                line = line.strip()
                if not line:
                    continue
                if log_level != 'ALL':
                    # Log entries look like: [2026-06-24 13:17:55] INFO ...
                    if f' {log_level} ' not in line:
                        continue
                log_lines.append(line)
                
            log_lines = log_lines[-lines_to_read:]
            log_lines.reverse()
        except Exception as err:
            log_lines = [f"Error reading log file: {str(err)}"]
    else:
        log_lines = ["No log file found yet. System logs will appear as actions occur."]
        
    return render_template('system_logs.html', log_lines=log_lines, current_limit=lines_to_read, current_level=log_level)

@app.route('/admin/system-logs/clear', methods=['POST'])
@admin_required
def clear_system_logs():
    try:
        if os.path.exists(log_file_path):
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] INFO [system]: Logs cleared by admin.\n")
            flash("System logs cleared successfully.", "success")
        else:
            flash("Log file does not exist.", "warning")
    except Exception as e:
        flash(f"Failed to clear logs: {str(e)}", "error")
    return redirect(url_for('view_system_logs'))

# --- Bucket Sharing APIs ---

@app.route('/api/bucket-share/info')
@login_required
def get_bucket_share_info():
    connection_id = request.args.get('connection_id')
    bucket_name = request.args.get('bucket_name')
    
    if not connection_id or not bucket_name:
        return jsonify({'status': 'error', 'message': 'Missing connection_id or bucket_name'}), 400
        
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    # Verify caller is Admin or Owner of the bucket
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None
    
    if g.user.role != 'Admin' and owner_id != g.user.id:
        return jsonify({'status': 'error', 'message': 'Permission Denied. Only owners can share.'}), 403
        
    owner_user = db.session.get(User, owner_id) if owner_id else None
    
    # Get all shared users
    accesses = BucketAccess.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).all()
    shared_users = []
    for access in accesses:
        shared_users.append({
            'id': access.user.id,
            'name': access.user.name,
            'email': access.user.email,
            'role': access.role
        })
        
    # Share link
    share_link = url_for('browse_bucket', connection_id=connection_id, bucket_name=bucket_name, _external=True)
    
    return jsonify({
        'status': 'success',
        'bucket_name': bucket_name,
        'access_type': mapping.access_type if mapping else 'restricted',
        'owner': {
            'id': owner_user.id if owner_user else None,
            'name': owner_user.name if owner_user else 'System',
            'email': owner_user.email if owner_user else 'system@example.com'
        } if owner_user else None,
        'shared_users': shared_users,
        'share_link': share_link
    })

@app.route('/api/users/search')
@login_required
def search_users():
    query_str = request.args.get('q', '').strip()
    if not query_str:
        return jsonify([])
        
    # Search users by name or email, exclude Admins and current user
    users = User.query.filter(
        (User.role != 'Admin') & 
        (User.id != g.user.id) & 
        ((User.name.like(f'%{query_str}%')) | (User.email.like(f'%{query_str}%')))
    ).limit(10).all()
    
    return jsonify([{
        'id': u.id,
        'name': u.name,
        'email': u.email
    } for u in users])

@app.route('/api/bucket-share/add', methods=['POST'])
@login_required
def add_bucket_share():
    data = request.get_json() or {}
    connection_id = data.get('connection_id')
    bucket_name = data.get('bucket_name')
    user_id_or_email = data.get('user_id_or_email')
    role = data.get('role', 'Viewer')
    
    if not all([connection_id, bucket_name, user_id_or_email]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
        
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None
    
    if g.user.role != 'Admin' and owner_id != g.user.id:
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    # Find user by ID or Email
    target_user = None
    if isinstance(user_id_or_email, int) or str(user_id_or_email).isdigit():
        target_user = db.session.get(User, int(user_id_or_email))
    else:
        target_user = User.query.filter_by(email=user_id_or_email).first()
        
    if not target_user:
        return jsonify({'status': 'error', 'message': 'User not found.'}), 404
        
    if target_user.id == owner_id:
        return jsonify({'status': 'error', 'message': 'Cannot share with the owner.'}), 400
        
    # Check if already shared
    access = BucketAccess.query.filter_by(user_id=target_user.id, connection_id=conn.id, bucket_name=bucket_name).first()
    if access:
        access.role = role
    else:
        access = BucketAccess(user_id=target_user.id, connection_id=conn.id, bucket_name=bucket_name, role=role)
        db.session.add(access)
        
    db.session.commit()
    
    log_action(
        g.user.id,
        target_user.id,
        conn.name,
        bucket_name,
        'GRANT_ACCESS',
        f"Shared bucket '{bucket_name}' with {target_user.name} as {role}"
    )
    
    return jsonify({
        'status': 'success',
        'user': {
            'id': target_user.id,
            'name': target_user.name,
            'email': target_user.email,
            'role': role
        }
    })

@app.route('/api/bucket-share/update-role', methods=['POST'])
@login_required
def update_bucket_share_role():
    data = request.get_json() or {}
    connection_id = data.get('connection_id')
    bucket_name = data.get('bucket_name')
    target_user_id = data.get('user_id')
    role = data.get('role') # 'Viewer', 'Editor', or 'remove'
    
    if not all([connection_id, bucket_name, target_user_id, role]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
        
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None
    
    if g.user.role != 'Admin' and owner_id != g.user.id:
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    access = BucketAccess.query.filter_by(user_id=target_user_id, connection_id=conn.id, bucket_name=bucket_name).first()
    if not access:
        return jsonify({'status': 'error', 'message': 'Share record not found.'}), 404
        
    target_user = db.session.get(User, target_user_id)
    
    if role == 'remove':
        # Delete video progress for this user, connection, and bucket
        VideoProgress.query.filter_by(
            user_id=target_user_id,
            connection_name=conn.name,
            bucket_name=bucket_name
        ).delete()
        db.session.delete(access)
        db.session.commit()
        
        log_action(
            g.user.id,
            target_user_id,
            conn.name,
            bucket_name,
            'REVOKE_ACCESS',
            f"Revoked access to bucket '{bucket_name}' for {target_user.name if target_user else target_user_id}"
        )
    else:
        access.role = role
        db.session.commit()
        
        log_action(
            g.user.id,
            target_user_id,
            conn.name,
            bucket_name,
            'UPDATE_ACCESS',
            f"Updated role for {target_user.name if target_user else target_user_id} on bucket '{bucket_name}' to {role}"
        )
        
    return jsonify({'status': 'success'})

@app.route('/api/bucket-share/update-general-access', methods=['POST'])
@login_required
def update_bucket_general_access():
    data = request.get_json() or {}
    connection_id = data.get('connection_id')
    bucket_name = data.get('bucket_name')
    access_type = data.get('access_type') # 'restricted', 'public', 'public_view' or 'public_edit'
    
    if not all([connection_id, bucket_name, access_type]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
        
    if access_type not in ['restricted', 'public', 'public_view', 'public_edit']:
        return jsonify({'status': 'error', 'message': 'Invalid access type'}), 400
        
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None
    
    if g.user.role != 'Admin' and owner_id != g.user.id:
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    if not mapping:
        # Create user bucket mapping if it doesn't exist (e.g. legacy bucket)
        mapping = UserBucket(user_id=g.user.id, connection_id=conn.id, bucket_name=bucket_name)
        db.session.add(mapping)
        
    mapping.access_type = access_type
    db.session.commit()
    
    log_action(
        g.user.id,
        g.user.id,
        conn.name,
        bucket_name,
        'UPDATE_GENERAL_ACCESS',
        f"Updated general access of bucket '{bucket_name}' to {access_type}"
    )
    
    return jsonify({'status': 'success'})

@app.route('/api/connection/<connection_id>/bucket/<bucket_name>/files')
def api_bucket_files(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit']
    
    if not is_public and g.user is None:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        
    if not check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
    try:
        s3 = get_s3_client(conn)
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name)
        
        # Load progress records for the user
        progress_map = {}
        if g.user:
            progresses = VideoProgress.query.filter_by(
                user_id=g.user.id,
                connection_name=conn.name,
                bucket_name=bucket_name
            ).all()
            progress_map = {p.file_key: {
                'seconds': p.seconds_watched,
                'duration': p.duration,
                'pct': round(p.seconds_watched / p.duration * 100, 1) if p.duration > 0 else 0
            } for p in progresses}
        
        files = []
        for page in pages:
            for obj in page.get('Contents', []):
                key = obj.get('Key')
                # Skip folders if listed as empty directories (keys ending with '/')
                if key.endswith('/'):
                    continue
                filename = key.split('/')[-1]
                ext = filename.split('.')[-1].lower() if '.' in filename else ''
                
                # Determine type
                video_exts = ['mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
                audio_exts = ['mp3', 'wav', 'ogg', 'aac', 'flac']
                pdf_exts = ['pdf']
                ppt_exts = ['ppt', 'pptx']
                docx_exts = ['doc', 'docx']
                image_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']
                text_exts = ['txt', 'json', 'csv', 'xml', 'log', 'ini', 'cfg', 'yaml', 'yml', 'md', 'html', 'js', 'css']
                
                file_type = 'unknown'
                if ext in video_exts:
                    file_type = 'video'
                elif ext in audio_exts:
                    file_type = 'audio'
                elif ext in pdf_exts:
                    file_type = 'pdf'
                elif ext in ppt_exts:
                    file_type = 'powerpoint'
                elif ext in docx_exts:
                    file_type = 'docx'
                elif ext in image_exts:
                    file_type = 'image'
                elif ext in text_exts:
                    file_type = 'text'
                    
                files.append({
                    'key': key,
                    'name': filename,
                    'size': obj.get('Size'),
                    'last_modified': obj.get('LastModified').isoformat() if obj.get('LastModified') else None,
                    'file_type': file_type,
                    'progress': progress_map.get(key)
                })
        return jsonify({'status': 'success', 'files': files})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/connection/<connection_id>/bucket/<bucket_name>/share', methods=['POST'])
@login_required
def api_share_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
    
    data = request.get_json() or {}
    key = data.get('key')
    expires_in = data.get('expires_in', 3600)  # default 1 hour
    
    if not key:
        return jsonify({'status': 'error', 'message': 'Missing file key'}), 400
        
    try:
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = get_s3_client(conn, endpoint_url=public_endpoint)
        presigned_url = fix_s3_url(s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=int(expires_in)
        ))
        return jsonify({'status': 'success', 'share_link': presigned_url})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/video/notes', methods=['GET'])
@login_required
def get_video_notes():
    connection_name = request.args.get('connection_name')
    bucket_name = request.args.get('bucket_name')
    file_key = request.args.get('file_key')
    
    if not all([connection_name, bucket_name, file_key]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
        
    try:
        notes = VideoNote.query.filter_by(
            user_id=g.user.id,
            connection_name=connection_name,
            bucket_name=bucket_name,
            file_key=file_key
        ).order_by(VideoNote.timestamp.asc()).all()
        
        notes_list = [{
            'id': note.id,
            'timestamp': note.timestamp,
            'content': note.content,
            'created_at': note.created_at.isoformat()
        } for note in notes]
        
        return jsonify({'status': 'success', 'notes': notes_list})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/video/notes', methods=['POST'])
@login_required
def create_video_note():
    data = request.get_json() or {}
    connection_name = data.get('connection_name')
    bucket_name = data.get('bucket_name')
    file_key = data.get('file_key')
    timestamp = data.get('timestamp')
    content = data.get('content')
    
    if not all([connection_name, bucket_name, file_key, content]) or timestamp is None:
        return jsonify({'status': 'error', 'message': 'Missing fields'}), 400
        
    try:
        note = VideoNote(
            user_id=g.user.id,
            connection_name=connection_name,
            bucket_name=bucket_name,
            file_key=file_key,
            timestamp=float(timestamp),
            content=content.strip()
        )
        db.session.add(note)
        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'note': {
                'id': note.id,
                'timestamp': note.timestamp,
                'content': note.content,
                'created_at': note.created_at.isoformat()
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/video/notes/<int:note_id>', methods=['DELETE'])
@login_required
def delete_video_note(note_id):
    note = VideoNote.query.filter_by(id=note_id, user_id=g.user.id).first_or_404()
    try:
        db.session.delete(note)
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Note deleted'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- Remote Link Download Background Handler and Routes ---

def background_remote_download(app_to_use, task_id):
    import time
    import tempfile
    import requests
    from botocore.exceptions import ClientError
    import traceback
    
    app_to_use.logger.info(f"[Remote Thread] background_remote_download spawned for task_id={task_id}")
    with app_to_use.app_context():
        app_to_use.logger.info(f"[Remote Thread] Context entered for task_id={task_id}")
        temp_dir = tempfile.gettempdir()
        local_filename = None
        task = None
        
        try:
            app_to_use.logger.info(f"[Remote Thread] Querying DB for task_id={task_id}")
            # Retry fetching task from DB in case of transaction visibility delay (race condition)
            for attempt in range(5):
                task = db.session.get(RemoteTask, task_id)
                if task:
                    app_to_use.logger.info(f"[Remote Thread] Found task on attempt {attempt}")
                    break
                app_to_use.logger.info(f"[Remote Thread] Task not found on attempt {attempt}. Retrying...")
                time.sleep(0.5)
                db.session.expire_all()
                
            if not task:
                app_to_use.logger.error(f"[Remote Thread] Remote Task {task_id} not found in database after retries.")
                return
            
            app_to_use.logger.info(f"[Remote Thread] Querying S3 connection for ID={task.connection_id}")
            conn = db.session.get(S3Connection, task.connection_id)
            if not conn:
                app_to_use.logger.error(f"[Remote Thread] S3 Connection not found for task_id={task_id}")
                task.status = 'failed'
                task.error_message = 'S3 Connection not found.'
                db.session.commit()
                return
                
            app_to_use.logger.info(f"[Remote Thread] Setting task status to downloading for task_id={task_id}")
            task.status = 'downloading'
            db.session.commit()
            
            # Phase 1: Download
            if task.link_type == 'direct':
                # Direct file download
                r_head = requests.head(task.link, allow_redirects=True, timeout=15)
                content_len = int(r_head.headers.get('content-length', 0))
                
                filename_header = r_head.headers.get('content-disposition', '')
                if 'filename=' in filename_header:
                    filename = filename_header.split('filename=')[-1].strip('"\'')
                else:
                    filename = task.link.split('/')[-1].split('?')[0]
                
                if not filename:
                    filename = f"download_{int(time.time())}"
                
                filename_secured = secure_filename(filename)
                task.filename = filename_secured
                db.session.commit()
                
                local_filename = os.path.join(temp_dir, f"{task_id}_{filename_secured}")
                
                r = requests.get(task.link, stream=True, timeout=30)
                r.raise_for_status()
                
                downloaded = 0
                start_time = time.time()
                
                with open(local_filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            elapsed = time.time() - start_time
                            speed_val = downloaded / (elapsed if elapsed > 0 else 0.001)
                            speed_str = f"{round(speed_val / (1024*1024), 2)} MB/s" if speed_val > 1024*1024 else f"{round(speed_val / 1024, 1)} KB/s"
                            
                            pct = int((downloaded / content_len) * 100) if content_len > 0 else 50
                            
                            task.progress = min(pct, 99)
                            task.speed = speed_str
                            db.session.commit()
                            
            elif task.link_type == 'video':
                # Video download via yt-dlp
                def yt_progress_hook(d):
                    if d['status'] == 'downloading':
                        speed_val = d.get('speed', 0)
                        if speed_val:
                            speed_str = f"{round(speed_val / (1024*1024), 2)} MB/s" if speed_val > 1024*1024 else f"{round(speed_val / 1024, 1)} KB/s"
                        else:
                            speed_str = 'Calculating...'
                        
                        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                        downloaded = d.get('downloaded_bytes', 0)
                        pct = int((downloaded / total_bytes) * 100) if total_bytes > 0 else 0
                        
                        task.progress = min(pct, 99)
                        task.speed = speed_str
                        db.session.commit()
                        
                outtmpl = os.path.join(temp_dir, f"{task_id}_%(title)s.%(ext)s")
                ydl_opts = {
                    'outtmpl': outtmpl,
                    'progress_hooks': [yt_progress_hook],
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'quiet': True,
                    'no_warnings': True
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(task.link, download=True)
                    temp_filepath = ydl.prepare_filename(info)
                    
                    if not os.path.exists(temp_filepath):
                        files = os.listdir(temp_dir)
                        matching = [f for f in files if f.startswith(task_id)]
                        if matching:
                            temp_filepath = os.path.join(temp_dir, matching[0])
                        else:
                            raise Exception("Could not find downloaded file.")
                    
                    local_filename = temp_filepath
                    task.filename = os.path.basename(temp_filepath).replace(f"{task_id}_", "")
                    db.session.commit()
                    
            elif task.link_type == 'torrent':
                # Torrent download via libtorrent
                import libtorrent as lt
                ses = lt.session()
                ses.listen_on(6881, 6891)
                
                if task.link.startswith('magnet:'):
                    params = lt.parse_magnet_uri(task.link)
                    params.save_path = temp_dir
                    handle = ses.add_torrent(params)
                    
                    task.speed = "Downloading Metadata..."
                    db.session.commit()
                    
                    while not handle.has_metadata():
                        time.sleep(1)
                        task_chk = db.session.get(RemoteTask, task_id)
                        if not task_chk or task_chk.status == 'failed':
                            return
                else:
                    r_torrent = requests.get(task.link, timeout=20)
                    r_torrent.raise_for_status()
                    info = lt.torrent_info(lt.bdecode(r_torrent.content))
                    params = {
                        'ti': info,
                        'save_path': temp_dir
                    }
                    handle = ses.add_torrent(params)
                
                torrent_info = handle.get_torrent_info()
                torrent_name = torrent_info.name()
                task.filename = torrent_name
                db.session.commit()
                
                while not handle.status().is_seeding:
                    s = handle.status()
                    pct = int(s.progress * 100)
                    
                    speed_val = s.download_rate
                    speed_str = f"{round(speed_val / (1024*1024), 2)} MB/s" if speed_val > 1024*1024 else f"{round(speed_val / 1024, 1)} KB/s"
                    
                    task.progress = min(pct, 99)
                    task.speed = speed_str
                    db.session.commit()
                    
                    time.sleep(1.5)
                    
                    task_chk = db.session.get(RemoteTask, task_id)
                    if not task_chk or task_chk.status == 'failed':
                        return
                
                local_filename = os.path.join(temp_dir, torrent_name)
                
            else:
                raise Exception(f"Unsupported link type: {task.link_type}")
                
            # Phase 2: Upload to S3
            if not local_filename or not os.path.exists(local_filename):
                raise Exception("Downloaded file not found on server.")
                
            task.status = 'uploading'
            task.progress = 0
            task.speed = 'Uploading to S3...'
            db.session.commit()
            
            is_dir = os.path.isdir(local_filename)
            endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
            s3 = get_s3_client(conn, endpoint_url=endpoint_url)
            
            def upload_file_to_s3(local_path, s3_key):
                file_size = os.path.getsize(local_path)
                
                class ProgressPercentage(object):
                    def __init__(self, filename):
                        self._filename = filename
                        self._size = file_size
                        self._seen_so_far = 0
                        self._lock = threading.Lock()
                    def __call__(self, bytes_amount):
                        with self._lock:
                            self._seen_so_far += bytes_amount
                            pct = int((self._seen_so_far / self._size) * 100) if self._size > 0 else 0
                            task.progress = min(pct, 99)
                            db.session.commit()
                            
                s3.upload_file(
                    local_path,
                    task.bucket_name,
                    s3_key,
                    Callback=ProgressPercentage(local_path)
                )
                
                existing_upload = UploadedFile.query.filter_by(
                    connection_id=conn.id,
                    bucket_name=task.bucket_name,
                    file_key=s3_key
                ).first()
                if existing_upload:
                    existing_upload.user_id = task.user_id
                    existing_upload.created_at = datetime.utcnow()
                else:
                    uploaded_file = UploadedFile(
                        connection_id=conn.id,
                        bucket_name=task.bucket_name,
                        file_key=s3_key,
                        user_id=task.user_id
                    )
                    db.session.add(uploaded_file)
                db.session.commit()
                
                ext = s3_key.split('.')[-1].lower() if '.' in s3_key else ''
                is_previewable = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
                if is_previewable:
                    file_cat = 'image' if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'] else 'video'
                    try:
                        threading.Thread(
                            target=generate_and_cache_preview_task,
                            args=(app_to_use, conn.id, task.bucket_name, s3_key, file_cat)
                        ).start()
                    except Exception:
                        pass
            
            if is_dir:
                uploaded_something = False
                base_dir = os.path.dirname(local_filename)
                for root, dirs, files in os.walk(local_filename):
                    for file in files:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, base_dir).replace('\\', '/')
                        s3_key = task.prefix + rel_path
                        upload_file_to_s3(full_path, s3_key)
                        uploaded_something = True
                if not uploaded_something:
                    raise Exception("No files found inside torrent directory to upload.")
            else:
                s3_key = task.prefix + task.filename
                upload_file_to_s3(local_filename, s3_key)
                
            task.status = 'completed'
            task.progress = 100
            task.speed = ''
            db.session.commit()
            
        except Exception as e:
            if task:
                task.status = 'failed'
                task.error_message = str(e)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            app_to_use.logger.error(f"Error in remote download task {task_id}: {e}\n{traceback.format_exc()}")
            
        finally:
            if local_filename and os.path.exists(local_filename):
                try:
                    if os.path.isdir(local_filename):
                        import shutil
                        shutil.rmtree(local_filename)
                    else:
                        os.remove(local_filename)
                except Exception as ex:
                    print(f"Warning: Cleanup failed for {local_filename}: {ex}")

@app.route('/connection/<connection_id>/bucket/<bucket_name>/remote-download', methods=['POST'])
@login_required
def remote_download(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    data = request.get_json() or {}
    link = data.get('link', '').strip()
    link_type = data.get('type', 'direct')
    prefix = data.get('prefix', '')

    if not link:
        return jsonify({'status': 'error', 'message': 'Download link cannot be empty.'}), 400

    app.logger.info(f"[Remote Download] Creating task for link={link}, type={link_type}, bucket={bucket_name}")
    task_id = str(uuid.uuid4())
    task = RemoteTask(
        id=task_id,
        user_id=g.user.id,
        connection_id=conn.id,
        bucket_name=bucket_name,
        prefix=prefix,
        link=link,
        link_type=link_type,
        status='pending'
    )
    db.session.add(task)
    db.session.commit()
    app.logger.info(f"[Remote Download] Task created in DB with ID={task_id}. Starting background thread...")

    t = threading.Thread(
        target=background_remote_download,
        args=(app, task_id)
    )
    t.daemon = True
    t.start()
    app.logger.info(f"[Remote Download] Background thread started for task_id={task_id}")

    return jsonify({
        'status': 'success',
        'task_id': task_id,
        'message': 'Download started in background.'
    })

@app.route('/api/tasks/<task_id>', methods=['GET'])
@login_required
def get_task_status(task_id):
    task = RemoteTask.query.filter_by(id=task_id, user_id=g.user.id).first_or_404()
    return jsonify({
        'status': 'success',
        'task': {
            'id': task.id,
            'link_type': task.link_type,
            'status': task.status,
            'progress': task.progress,
            'speed': task.speed,
            'filename': task.filename or 'Preparing...',
            'error_message': task.error_message
        }
    })

@app.route('/remote-tasks')
@login_required
def list_remote_tasks():
    tasks = RemoteTask.query.filter_by(user_id=g.user.id).order_by(RemoteTask.created_at.desc()).all()
    return render_template('tasks.html', tasks=tasks)

@app.route('/remote-tasks/<task_id>')
@login_required
def list_remote_task_details(task_id):
    task = RemoteTask.query.filter_by(id=task_id, user_id=g.user.id).first_or_404()
    return render_template('task_detail.html', task=task)


@app.route('/remote-tasks/<task_id>/delete', methods=['POST'])
@login_required
def delete_remote_task(task_id):
    task = RemoteTask.query.filter_by(id=task_id, user_id=g.user.id).first_or_404()
    try:
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted from history.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to delete task: {e}', 'error')
    return redirect(url_for('list_remote_tasks'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7090, debug=True)

