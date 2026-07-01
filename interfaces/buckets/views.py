from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from extensions import db
from infrastructure.persistence.models import BucketAccess, ItemLike, S3Connection, UploadedFile, UserBucket, VideoProgress
from interfaces.middleware.context import login_required
from use_cases.access_control import (
    check_bucket_access as access_control_check_bucket_access,
    check_bucket_edit_access as access_control_check_bucket_edit_access,
)
from use_cases.audit import log_action as audit_log_action


bp = Blueprint('buckets', __name__)


def _get_s3_client(connection, endpoint_url=None):
    return current_app.config['GET_S3_CLIENT'](connection, endpoint_url=endpoint_url)


def _fix_s3_url(url):
    return current_app.config['FIX_S3_URL'](url)


def _configure_bucket_cors(s3_client, bucket_name):
    return current_app.config['CONFIGURE_BUCKET_CORS'](s3_client, bucket_name)


def _log_action(actor_id, target_user_id, connection_name, bucket_name, action_type, details):
    return audit_log_action(
        actor_id,
        target_user_id,
        connection_name,
        bucket_name,
        action_type,
        details,
        db_session=db.session,
    )


@bp.route('/connection/<connection_id>/bucket/create', methods=['POST'])
@login_required
def create_bucket(connection_id):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    bucket_name = request.form.get('bucket_name', '').strip().lower()

    if not bucket_name:
        flash('Bucket name cannot be empty.', 'error')
        return redirect(url_for('connections.view_connection', connection_id=connection_id))

    try:
        s3 = _get_s3_client(conn)
        kwargs = {'Bucket': bucket_name}
        if conn.region_name and conn.region_name != 'us-east-1' and conn.endpoint_url and 'amazonaws.com' in conn.endpoint_url:
            kwargs['CreateBucketConfiguration'] = {'LocationConstraint': conn.region_name}

        s3.create_bucket(**kwargs)
        _configure_bucket_cors(s3, bucket_name)

        db.session.add(UserBucket(user_id=g.user.id, connection_id=conn.id, bucket_name=bucket_name))
        db.session.commit()

        _log_action(g.user.id, g.user.id, conn.name, bucket_name, 'CREATE_BUCKET', f"Created bucket '{bucket_name}'")
        flash(f'Bucket "{bucket_name}" created successfully.', 'success')
    except Exception as exc:
        flash(f'Failed to create bucket: {str(exc)}', 'error')

    return redirect(url_for('connections.view_connection', connection_id=connection_id))


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/delete', methods=['POST'])
@login_required
def delete_bucket(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    if g.user.role != 'Admin' and owner_id != g.user.id:
        flash('Permission Denied. You do not own this bucket.', 'error')
        return redirect(url_for('connections.view_connection', connection_id=connection_id))

    try:
        s3 = _get_s3_client(conn)
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
            try:
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket_name):
                    delete_keys = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
                    if delete_keys:
                        s3.delete_objects(Bucket=bucket_name, Delete={'Objects': delete_keys})
            except Exception:
                pass

        s3.delete_bucket(Bucket=bucket_name)
        VideoProgress.query.filter_by(connection_name=conn.name, bucket_name=bucket_name).delete()
        if mapping:
            db.session.delete(mapping)
        BucketAccess.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).delete()
        db.session.commit()

        details = 'Deleted bucket owned by user' if g.user.id == owner_id else f"Admin {g.user.name} deleted user's bucket"
        _log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_BUCKET', details)
        flash(f'Bucket "{bucket_name}" deleted successfully.', 'success')
    except Exception as exc:
        flash(f'Failed to delete bucket: {str(exc)}', 'error')

    return redirect(url_for('connections.view_connection', connection_id=connection_id))


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/browse')
def browse_bucket(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    prefix = request.args.get('prefix', '')
    sort_by = request.args.get('sort', 'name')
    direction = request.args.get('direction', 'asc')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('auth.login'))

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        flash('Permission Denied. You do not have access to this bucket.', 'error')
        return redirect(url_for('connections.view_connection', connection_id=connection_id))

    owner_id = mapping.user_id if mapping else None

    try:
        s3 = _get_s3_client(conn)
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3_public = _get_s3_client(conn, endpoint_url=public_endpoint)
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')

        folders = []
        files = []

        progress_map = {}
        if g.user:
            progresses = VideoProgress.query.filter_by(user_id=g.user.id, connection_name=conn.name, bucket_name=bucket_name).all()
            progress_map = {progress.file_key: progress for progress in progresses}

        uploaded_files = UploadedFile.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).all()
        creator_map = {uploaded.file_key: uploaded.user.name for uploaded in uploaded_files}

        for page in pages:
            for common_prefix in page.get('CommonPrefixes', []):
                folders.append(common_prefix.get('Prefix'))

            for obj in page.get('Contents', []):
                if obj.get('Key') == prefix:
                    continue
                key = obj.get('Key')
                progress = progress_map.get(key)
                ext = key.split('.')[-1].lower() if '.' in key else ''
                is_previewable = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'ogg', 'mkv', 'mov', 'flv']
                url = None
                if is_previewable:
                    try:
                        url = _fix_s3_url(s3_public.generate_presigned_url('get_object', Params={'Bucket': bucket_name, 'Key': key}, ExpiresIn=3600))
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
                        'seconds': progress.seconds_watched,
                        'duration': progress.duration,
                        'pct': round(progress.seconds_watched / progress.duration * 100, 1) if (progress and progress.duration > 0) else 0,
                    } if progress else None,
                })

        reverse_sort = direction == 'desc'
        folders.sort(key=lambda item: item.lower(), reverse=reverse_sort)
        if sort_by == 'size':
            files.sort(key=lambda item: item.get('size') or 0, reverse=reverse_sort)
        elif sort_by == 'last_modified':
            from datetime import datetime, timezone
            min_dt = datetime.min.replace(tzinfo=timezone.utc)
            files.sort(key=lambda item: item.get('last_modified') or min_dt, reverse=reverse_sort)
        else:
            files.sort(key=lambda item: item.get('name', '').lower(), reverse=reverse_sort)

        can_edit = access_control_check_bucket_edit_access(g.user, conn, bucket_name)
        likes = ItemLike.query.filter_by(connection_name=conn.name, bucket_name=bucket_name).all()
        likes_map = {like.file_key: like.like_count for like in likes}

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
            direction=direction,
            likes_map=likes_map,
        )
    except Exception as exc:
        flash(f'Failed to browse bucket contents: {str(exc)}', 'error')
        return redirect(url_for('connections.view_connection', connection_id=connection_id))
