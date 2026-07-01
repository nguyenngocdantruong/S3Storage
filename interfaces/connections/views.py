from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from extensions import db
from infrastructure.persistence.models import BucketAccess, S3Connection, UploadedFile, UserBucket, VideoProgress
from interfaces.middleware.context import admin_required
from use_cases.access_control import check_bucket_access as access_control_check_bucket_access
from use_cases.slug import generate_unique_slug


bp = Blueprint('connections', __name__)


def _get_s3_client(connection, endpoint_url=None):
    return current_app.config['GET_S3_CLIENT'](connection, endpoint_url=endpoint_url)


def _get_bucket_size(s3_client, bucket_name):
    return current_app.config['GET_BUCKET_SIZE'](s3_client, bucket_name)


@bp.route('/connection/add', methods=['POST'])
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
        return redirect(url_for('main.dashboard'))

    existing_slugs = [conn.connection_id for conn in S3Connection.query.with_entities(S3Connection.connection_id).all()]
    connection_id = generate_unique_slug(name, existing_slugs, requested_slug=connection_id)

    try:
        conn_temp = S3Connection(
            connection_id=connection_id,
            name=name,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region_name=region_name,
            upload_endpoint=upload_endpoint,
            owner_id=g.user.id if g.user else None,
        )
        s3 = _get_s3_client(conn_temp)
        try:
            s3.list_buckets()
        except Exception as exc:
            status_code = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            error_code = exc.response.get('Error', {}).get('Code')
            if status_code != 403 and error_code not in ['AccessDenied', 'AllAccessDisabled']:
                raise exc

        db.session.add(conn_temp)
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'success', 'message': 'S3 Connection added successfully!'})

        flash('S3 Connection added successfully!', 'success')
    except Exception as exc:
        db.session.rollback()
        error_msg = str(exc)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': f'S3 connection test error: {error_msg}'}), 400
        flash(f'S3 connection test error: {error_msg}', 'error')

    return redirect(url_for('main.dashboard'))


@bp.route('/connection/<connection_id>/delete', methods=['POST'])
@admin_required
def delete_connection(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    try:
        UserBucket.query.filter_by(connection_id=conn.id).delete()
        BucketAccess.query.filter_by(connection_id=conn.id).delete()
        VideoProgress.query.filter_by(connection_name=conn.name).delete()
        UploadedFile.query.filter_by(connection_id=conn.id).delete()
        db.session.delete(conn)
        db.session.commit()
        flash('Connection deleted successfully.', 'success')
    except Exception as exc:
        flash(f'Error: {str(exc)}', 'error')
    return redirect(url_for('main.dashboard'))


@bp.route('/connection/<connection_id>/edit', methods=['POST'])
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
        return redirect(url_for('main.dashboard'))

    try:
        old_name = conn.name
        conn_test = S3Connection(
            name=name,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region_name=region_name,
            upload_endpoint=upload_endpoint,
        )
        s3 = _get_s3_client(conn_test)
        try:
            s3.list_buckets()
        except Exception as exc:
            status_code = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            error_code = exc.response.get('Error', {}).get('Code')
            if status_code != 403 and error_code not in ['AccessDenied', 'AllAccessDisabled']:
                raise exc

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
    except Exception as exc:
        db.session.rollback()
        error_msg = str(exc)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'error', 'message': f'S3 connection test error: {error_msg}'}), 400
        flash(f'S3 connection test error: {error_msg}', 'error')

    return redirect(url_for('main.dashboard'))


@bp.route('/connection/<connection_id>')
def view_connection(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    try:
        s3 = _get_s3_client(conn)

        raw_buckets = []
        try:
            response = s3.list_buckets()
            raw_buckets = response.get('Buckets', [])
        except Exception as exc:
            if exc.response.get('Error', {}).get('Code') in ['AccessDenied', '403'] or 'Forbidden' in str(exc):
                if g.user and g.user.role == 'Admin':
                    mappings = UserBucket.query.filter_by(connection_id=conn.id).all()
                    shared = BucketAccess.query.filter_by(connection_id=conn.id).all()
                elif g.user:
                    mappings = UserBucket.query.filter_by(connection_id=conn.id, user_id=g.user.id).all()
                    shared = BucketAccess.query.filter_by(connection_id=conn.id, user_id=g.user.id).all()
                else:
                    mappings = UserBucket.query.filter_by(connection_id=conn.id).filter(UserBucket.access_type.in_(['public', 'public_view', 'public_edit', 'public_upload'])).all()
                    shared = []

                mapped_names = set([mapping.bucket_name for mapping in mappings] + [share.bucket_name for share in shared])
                raw_buckets = [{'Name': name, 'CreationDate': None} for name in mapped_names]
                flash('Unable to list all buckets (403 Forbidden). Only displaying buckets you are authorized to access.', 'warning')
            else:
                raise exc

        mappings = UserBucket.query.filter_by(connection_id=conn.id).all()
        owner_map = {mapping.bucket_name: mapping.user for mapping in mappings}

        buckets = []
        for bucket in raw_buckets:
            name = bucket['Name']
            owner = owner_map.get(name)
            if access_control_check_bucket_access(g.user, conn, name):
                buckets.append({
                    'Name': name,
                    'CreationDate': bucket.get('CreationDate'),
                    'owner': owner,
                    'Size': _get_bucket_size(s3, name),
                })

        return render_template('buckets.html', connection=conn, buckets=buckets)
    except Exception as exc:
        flash(f'Error connecting to S3 storage: {str(exc)}', 'error')
        return redirect(url_for('main.dashboard'))
