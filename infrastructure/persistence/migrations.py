import os
import re

from extensions import db
from infrastructure.persistence.models import S3Connection, User, UserBucket


def _column_exists(query):
    try:
        db.session.execute(db.text(query)).fetchone()
        db.session.rollback()
        return True
    except Exception:
        db.session.rollback()
        return False


def _migrate_user_quota_limit():
    if _column_exists('SELECT quota_limit FROM user LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE user ADD COLUMN quota_limit INTEGER DEFAULT 2147483648'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error: {e}')


def _migrate_user_is_active():
    if _column_exists('SELECT is_active FROM user LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE user ADD COLUMN is_active BOOLEAN DEFAULT 1'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for is_active: {e}')


def _migrate_s3connection_connection_id():
    if _column_exists('SELECT connection_id FROM s3_connection LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE s3_connection ADD COLUMN connection_id VARCHAR(100)'))
        db.session.commit()
        conns = S3Connection.query.all()
        for conn in conns:
            slug = conn.name.lower().strip().replace(' ', '-')
            slug = re.sub(r'[^a-z0-9\-]', '', slug)
            if not slug:
                slug = f'conn-{conn.id}'
            base_slug = slug
            count = 1
            while S3Connection.query.filter_by(connection_id=slug).first():
                slug = f'{base_slug}-{count}'
                count += 1
            conn.connection_id = slug
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for s3_connection: {e}')


def _migrate_s3connection_upload_endpoint():
    if _column_exists('SELECT upload_endpoint FROM s3_connection LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE s3_connection ADD COLUMN upload_endpoint VARCHAR(255)'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for upload_endpoint: {e}')


def _migrate_s3connection_owner_id():
    if _column_exists('SELECT owner_id FROM s3_connection LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE s3_connection ADD COLUMN owner_id INTEGER REFERENCES user(id)'))
        db.session.commit()
        admin_user = User.query.filter_by(role='Admin').first()
        if admin_user:
            db.session.execute(db.text(f'UPDATE s3_connection SET owner_id = {admin_user.id}'))
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for owner_id: {e}')


def _migrate_userbucket_access_type():
    if _column_exists('SELECT access_type FROM user_bucket LIMIT 1'):
        return
    try:
        db.session.execute(db.text("ALTER TABLE user_bucket ADD COLUMN access_type VARCHAR(20) DEFAULT 'restricted'"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for user_bucket: {e}')


def _migrate_userbucket_bucket_size():
    if _column_exists('SELECT bucket_size FROM user_bucket LIMIT 1'):
        return
    try:
        db.session.execute(db.text('ALTER TABLE user_bucket ADD COLUMN bucket_size BIGINT DEFAULT 0'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for user_bucket bucket_size: {e}')


def _migrate_bucketaccess_role():
    if _column_exists('SELECT role FROM bucket_access LIMIT 1'):
        return
    try:
        db.session.execute(db.text("ALTER TABLE bucket_access ADD COLUMN role VARCHAR(20) DEFAULT 'Viewer'"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f'Migration error for bucket_access: {e}')


def _seed_admin_from_config(app_config, db_exists):
    db_is_empty = False
    try:
        db_is_empty = User.query.count() == 0
    except Exception:
        db_is_empty = True

    if db_exists and not db_is_empty:
        return

    admin_data = app_config.admin
    admin = User(
        name=admin_data['fullname'],
        email=admin_data['email'],
        dob=admin_data['dob'],
        role='Admin',
    )
    admin.set_password(admin_data['password'])
    db.session.add(admin)
    db.session.commit()


def _sync_unassigned_buckets_to_admin(get_s3_client, get_bucket_size):
    try:
        admin_user = User.query.filter_by(role='Admin').first()
        if not admin_user:
            return
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
                            bucket_size=size,
                        )
                        db.session.add(new_mapping)
                        print(f"Startup Sync: Mapped unassigned bucket '{bucket_name}' to Admin '{admin_user.name}' with size {size}")
                    elif mapping.user_id == admin_user.id:
                        size = get_bucket_size(s3, bucket_name) or 0
                        mapping.bucket_size = size
                        print(f"Startup Sync: Calculated and updated legacy bucket '{bucket_name}' size to {size}")
                db.session.commit()
            except Exception as conn_err:
                print(f'Startup Sync Warning: Failed to scan connection {conn.name}: {conn_err}')
    except Exception as sync_err:
        print(f'Startup Sync Error: {sync_err}')


def run_startup_migrations(app):
    project_root = app.config['PROJECT_ROOT']
    db_path = app.config['APP_CONFIG'].db_path
    db_exists = os.path.exists(db_path)
    get_s3_client = app.config['GET_S3_CLIENT']
    get_bucket_size = app.config['GET_BUCKET_SIZE']

    with app.app_context():
        db.create_all()
        try:
            db.session.execute(db.text('PRAGMA journal_mode=WAL;'))
            db.session.commit()
        except Exception as e:
            print(f'Failed to set WAL mode: {e}')

        _migrate_user_quota_limit()
        _migrate_user_is_active()
        _migrate_s3connection_connection_id()
        _migrate_s3connection_upload_endpoint()
        _migrate_s3connection_owner_id()
        _migrate_userbucket_access_type()
        _migrate_userbucket_bucket_size()
        _migrate_bucketaccess_role()
        _seed_admin_from_config(app.config['APP_CONFIG'], db_exists)
        _sync_unassigned_buckets_to_admin(get_s3_client, get_bucket_size)
