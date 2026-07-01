import os
import traceback

from flask import Blueprint, current_app, g, jsonify, request, url_for

from extensions import db
from infrastructure.storage.boto3_provider import s3_key_exists
from infrastructure.persistence.models import BucketAccess, ItemLike, S3Connection, S3FileIndex, UploadedFile, User, UserBucket, VideoNote, VideoProgress
from interfaces.middleware.context import login_required
from use_cases.access_control import (
    check_bucket_access as access_control_check_bucket_access,
    check_bucket_edit_access as access_control_check_bucket_edit_access,
    check_file_edit_access as access_control_check_file_edit_access,
)
from use_cases.audit import log_action as audit_log_action
from use_cases.file_ops import paste_single_file as file_ops_paste_single_file
from use_cases.file_type import classify_file_type


bp = Blueprint('api', __name__)


def _get_s3_client(connection, endpoint_url=None):
    return current_app.config['GET_S3_CLIENT'](connection, endpoint_url=endpoint_url)


def _fix_s3_url(url):
    return current_app.config['FIX_S3_URL'](url)


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


def _paste_single_file(src_conn, src_bucket, src_key, dest_conn, dest_bucket, dest_key, action):
    return file_ops_paste_single_file(
        src_conn,
        src_bucket,
        src_key,
        dest_conn,
        dest_bucket,
        dest_key,
        action,
        current_user_id=g.user.id if g.user else 1,
        db_session=db.session,
        storage_provider_factory=_get_s3_client,
    )


@bp.route('/api/bucket-share/info')
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
    share_link = url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name, _external=True)
    
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

@bp.route('/api/users/search')
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

@bp.route('/api/bucket-share/add', methods=['POST'])
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
    
    _log_action(
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

@bp.route('/api/bucket-share/update-role', methods=['POST'])
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
        
        _log_action(
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
        
        _log_action(
            g.user.id,
            target_user_id,
            conn.name,
            bucket_name,
            'UPDATE_ACCESS',
            f"Updated role for {target_user.name if target_user else target_user_id} on bucket '{bucket_name}' to {role}"
        )
        
    return jsonify({'status': 'success'})

@bp.route('/api/bucket-share/update-general-access', methods=['POST'])
@login_required
def update_bucket_general_access():
    data = request.get_json() or {}
    connection_id = data.get('connection_id')
    bucket_name = data.get('bucket_name')
    access_type = data.get('access_type') # 'restricted', 'public', 'public_view' or 'public_edit'
    
    if not all([connection_id, bucket_name, access_type]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
        
    if access_type not in ['restricted', 'public', 'public_view', 'public_edit', 'public_upload']:
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
    
    _log_action(
        g.user.id,
        g.user.id,
        conn.name,
        bucket_name,
        'UPDATE_GENERAL_ACCESS',
        f"Updated general access of bucket '{bucket_name}' to {access_type}"
    )
    
    return jsonify({'status': 'success'})

@bp.route('/api/connection/<connection_id>/bucket/<bucket_name>/files')
def api_bucket_files(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']
    
    if not is_public and g.user is None:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        
    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
    try:
        s3 = _get_s3_client(conn)
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

@bp.route('/api/connection/<connection_id>/bucket/<bucket_name>/share', methods=['POST'])
@login_required
def api_share_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
    
    data = request.get_json() or {}
    key = data.get('key')
    expires_in = data.get('expires_in', 3600)  # default 1 hour
    
    if not key:
        return jsonify({'status': 'error', 'message': 'Missing file key'}), 400
        
    try:
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = _get_s3_client(conn, endpoint_url=public_endpoint)
        presigned_url = _fix_s3_url(s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=int(expires_in)
        ))
        return jsonify({'status': 'success', 'share_link': presigned_url})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/api/video/notes', methods=['GET'])
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

@bp.route('/api/video/notes', methods=['POST'])
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

@bp.route('/api/check-conflicts', methods=['POST'])
@login_required
def check_paste_conflicts():
    data = request.get_json() or {}
    dest_connection_id = data.get('dest_connection_id')
    dest_bucket_name = data.get('dest_bucket_name')
    dest_prefix = data.get('dest_prefix', '')
    items = data.get('items', [])
    
    if not dest_connection_id or not dest_bucket_name:
        return jsonify({'status': 'error', 'message': 'Missing destination Connection or Bucket'}), 400
        
    dest_conn = S3Connection.query.filter_by(connection_id=dest_connection_id).first_or_404()
    if not access_control_check_bucket_edit_access(g.user, dest_conn, dest_bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied at destination.'}), 403
        
    dest_s3 = _get_s3_client(dest_conn)
    conflicts = []

            
    for item in items:
        src_conn_id = item.get('connection_id')
        src_bucket = item.get('bucket_name')
        src_key = item.get('key')
        item_type = item.get('type')
        name = item.get('name')
        
        src_conn = S3Connection.query.filter_by(connection_id=src_conn_id).first()
        if not src_conn:
            continue
            
        src_s3 = _get_s3_client(src_conn)
        
        if item_type == 'folder':
            if src_conn_id == dest_connection_id and src_bucket == dest_bucket_name:
                if dest_prefix.startswith(src_key):
                    return jsonify({'status': 'error', 'message': 'Cannot copy/move a folder into itself or its subfolders.'}), 400

            paginator = src_s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=src_bucket, Prefix=src_key)
            for page in pages:
                for obj in page.get('Contents', []):
                    obj_key = obj['Key']
                    folder_name = src_key.rstrip('/').split('/')[-1]
                    rel_path = obj_key[len(src_key):]
                    target_key = f"{dest_prefix}{folder_name}/{rel_path}"
                    
                    if target_key.endswith('/'):
                        continue
                        
                    if s3_key_exists(dest_s3, dest_bucket_name, target_key):
                        size = obj.get('Size', 0)
                        last_modified = obj.get('LastModified')
                        last_modified_str = last_modified.strftime('%Y-%m-%d %H:%M:%S UTC') if last_modified else '?'
                        conflicts.append({
                            'source_key': obj_key,
                            'dest_key': target_key,
                            'name': target_key[len(dest_prefix):],
                            'size': size,
                            'last_modified': last_modified_str
                        })
        else:
            target_key = dest_prefix + name
            if s3_key_exists(dest_s3, dest_bucket_name, target_key):
                size = 0
                last_modified_str = '?'
                try:
                    meta = src_s3.head_object(Bucket=src_bucket, Key=src_key)
                    size = meta.get('ContentLength', 0)
                    last_mod = meta.get('LastModified')
                    if last_mod:
                        last_modified_str = last_mod.strftime('%Y-%m-%d %H:%M:%S UTC')
                except Exception:
                    pass
                    
                conflicts.append({
                    'source_key': src_key,
                    'dest_key': target_key,
                    'name': name,
                    'size': size,
                    'last_modified': last_modified_str
                })
                
    return jsonify({'status': 'success', 'conflicts': conflicts})

@bp.route('/api/paste', methods=['POST'])
@login_required
def paste_selected_items():
    data = request.get_json() or {}
    dest_connection_id = data.get('dest_connection_id')
    dest_bucket_name = data.get('dest_bucket_name')
    dest_prefix = data.get('dest_prefix', '')
    action = data.get('action', 'copy') # 'copy' or 'move'
    items = data.get('items', [])
    resolutions = data.get('resolutions', {}) # {source_key: 'replace' | 'keep_both' | 'skip'}
    
    if not dest_connection_id or not dest_bucket_name:
        return jsonify({'status': 'error', 'message': 'Missing destination parameters'}), 400
        
    dest_conn = S3Connection.query.filter_by(connection_id=dest_connection_id).first_or_404()
    if not access_control_check_bucket_edit_access(g.user, dest_conn, dest_bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied at destination.'}), 403
        
    dest_s3 = _get_s3_client(dest_conn)
    
    # Helper to check S3 key existence

            
    # Helper to compute a unique key for keep_both
    def get_unique_key(s3_client, bucket, key):
        if not s3_key_exists(s3_client, bucket, key):
            return key
        is_dir_key = key.endswith('/')
        if is_dir_key:
            base = key.rstrip('/')
            ext = '/'
        else:
            base, ext = os.path.splitext(key)
            
        counter = 1
        while True:
            candidate = f"{base} ({counter}){ext}"
            if not s3_key_exists(s3_client, bucket, candidate):
                return candidate
            counter += 1

    try:
        for item in items:
            src_conn_id = item.get('connection_id')
            src_bucket = item.get('bucket_name')
            src_key = item.get('key')
            item_type = item.get('type')
            name = item.get('name')
            
            src_conn = S3Connection.query.filter_by(connection_id=src_conn_id).first()
            if not src_conn:
                continue
                
            if action == 'move':
                if not access_control_check_file_edit_access(g.user, src_conn, src_bucket, src_key):
                    return jsonify({'status': 'error', 'message': 'Permission Denied. Báº¡n chá»‰ cÃ³ quyá»n di chuyá»ƒn nhá»¯ng file/thÆ° má»¥c do chÃ­nh mÃ¬nh táº£i lÃªn.'}), 403
            
            src_s3 = _get_s3_client(src_conn)
            
            # Skip if user tried pasting folder inside itself
            if item_type == 'folder' and src_conn_id == dest_connection_id and src_bucket == dest_bucket_name:
                if dest_prefix.startswith(src_key):
                    continue
            
            if item_type == 'folder':
                # S3 Folders: copy recursively
                folder_name = src_key.rstrip('/').split('/')[-1]
                
                target_folder_key = f"{dest_prefix}{folder_name}/"
                folder_res = resolutions.get(src_key)
                if folder_res == 'skip':
                    continue
                elif folder_res == 'keep_both':
                    target_folder_key = get_unique_key(dest_s3, dest_bucket_name, target_folder_key)
                    folder_name = target_folder_key.rstrip('/').split('/')[-1]
                
                paginator = src_s3.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=src_bucket, Prefix=src_key)
                
                for page in pages:
                    for obj in page.get('Contents', []):
                        obj_key = obj['Key']
                        rel_path = obj_key[len(src_key):]
                        target_key = f"{dest_prefix}{folder_name}/{rel_path}"
                        
                        if target_key.endswith('/'):
                            dest_s3.put_object(Bucket=dest_bucket_name, Key=target_key, Body=b'')
                            if action == 'move' and src_key != target_key:
                                src_s3.delete_object(Bucket=src_bucket, Key=obj_key)
                            continue
                            
                        file_res = resolutions.get(obj_key)
                        if file_res == 'skip':
                            continue
                        elif file_res == 'keep_both':
                            target_key = get_unique_key(dest_s3, dest_bucket_name, target_key)
                            
                        _paste_single_file(src_conn, src_bucket, obj_key, dest_conn, dest_bucket_name, target_key, action)
                        
                _log_action(
                    g.user.id, 
                    None, 
                    dest_conn.name, 
                    dest_bucket_name, 
                    f'{action.upper()}_FOLDER', 
                    f"{action.upper()} folder '{src_key}' to '{target_folder_key}'"
                )
                
            else: # file
                target_key = dest_prefix + name
                
                file_res = resolutions.get(src_key)
                if file_res == 'skip':
                    continue
                elif file_res == 'keep_both':
                    target_key = get_unique_key(dest_s3, dest_bucket_name, target_key)
                    
                _paste_single_file(src_conn, src_bucket, src_key, dest_conn, dest_bucket_name, target_key, action)
                
                _log_action(
                    g.user.id, 
                    None, 
                    dest_conn.name, 
                    dest_bucket_name, 
                    f'{action.upper()}_FILE', 
                    f"{action.upper()} file '{src_key}' to '{target_key}'"
                )
                
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Paste operation completed successfully.'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Paste Error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

def _paste_single_file(src_conn, src_bucket, src_key, dest_conn, dest_bucket, dest_key, action):
    return file_ops__paste_single_file(
        src_conn,
        src_bucket,
        src_key,
        dest_conn,
        dest_bucket,
        dest_key,
        action,
        current_user_id=g.user.id if g.user else 1,
        db_session=db.session,
        storage_provider_factory=get_s3_client,
    )

@bp.route('/api/connection/<connection_id>/bucket/<bucket_name>/check-existing', methods=['POST'])
@login_required
def check_existing_files(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
        
    data = request.get_json() or {}
    keys = data.get('keys', [])
    
    if not keys:
        return jsonify({'status': 'success', 'existing': []})
        
    try:
        s3 = _get_s3_client(conn)
        
        # Extract unique folder prefixes from keys
        prefixes = set()
        for key in keys:
            last_slash = key.rfind('/')
            prefix = key[:last_slash + 1] if last_slash != -1 else ''
            prefixes.add(prefix)
            
        # Bulk query directories on S3
        existing_set = set()
        for prefix in prefixes:
            paginator = s3.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
            for page in page_iterator:
                for obj in page.get('Contents', []):
                    existing_set.add(obj['Key'])
                    
        existing_keys = [key for key in keys if key in existing_set]
        return jsonify({'status': 'success', 'existing': existing_keys})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/api/connection/<connection_id>/bucket/<bucket_name>/resolve-unique-keys', methods=['POST'])
@login_required
def resolve_unique_keys(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403
        
    data = request.get_json() or {}
    keys = data.get('keys', [])
    
    resolved = {}
    if not keys:
        return jsonify({'status': 'success', 'resolved': {}})
        
    try:
        s3 = _get_s3_client(conn)
        
        # Extract unique folder prefixes
        prefixes = set()
        for key in keys:
            last_slash = key.rfind('/')
            prefix = key[:last_slash + 1] if last_slash != -1 else ''
            prefixes.add(prefix)
            
        # Bulk query directories on S3
        existing_set = set()
        for prefix in prefixes:
            paginator = s3.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
            for page in page_iterator:
                for obj in page.get('Contents', []):
                    existing_set.add(obj['Key'])
                    
        for key in keys:
            resolved_key = key
            if key in existing_set:
                is_dir = key.endswith('/')
                if is_dir:
                    base = key.rstrip('/')
                    ext = '/'
                else:
                    base, ext = os.path.splitext(key)
                counter = 1
                while True:
                    candidate = f"{base}_{counter}{ext}"
                    if candidate not in existing_set:
                        resolved_key = candidate
                        existing_set.add(candidate)  # Add resolved key to local set to prevent batch conflicts
                        break
                    counter += 1
            resolved[key] = resolved_key
        return jsonify({'status': 'success', 'resolved': resolved})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


