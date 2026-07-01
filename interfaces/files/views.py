import io
import os
from datetime import datetime
import traceback

from flask import Blueprint, current_app, flash, g, jsonify, redirect, request, url_for
from werkzeug.utils import secure_filename

from extensions import db
from infrastructure.persistence.models import S3Connection, UploadedFile, User, UserBucket, VideoNote, VideoProgress
from interfaces.middleware.context import login_required
from use_cases.access_control import (
    check_bucket_access as access_control_check_bucket_access,
    check_bucket_edit_access as access_control_check_bucket_edit_access,
    check_file_edit_access as access_control_check_file_edit_access,
)
from use_cases.audit import log_action as audit_log_action
from use_cases.quota import get_user_storage_used as quota_get_user_storage_used


bp = Blueprint('files', __name__)


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


def _get_user_storage_used(user):
    return quota_get_user_storage_used(user, db_session=db.session, storage_provider_factory=_get_s3_client)


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/initiate', methods=['POST'])
@login_required
def multipart_initiate(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
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
            used = _get_user_storage_used(quota_owner)
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
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
        
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

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/presign-part', methods=['POST'])
@login_required
def multipart_presign_part(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    data = request.get_json() or {}
    upload_id = data.get('uploadId')
    key = data.get('key')
    part_number = data.get('partNumber')

    if not all([upload_id, key, part_number]):
        return jsonify({'status': 'error', 'message': 'Missing uploadId, key, or partNumber.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
        
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
            'url': _fix_s3_url(presigned_url)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/complete', methods=['POST'])
@login_required
def multipart_complete(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
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
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
        
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
        
        _log_action(
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
        return jsonify({'status': 'success', 'size': actual_size})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/multipart/abort', methods=['POST'])
@login_required
def multipart_abort(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    data = request.get_json() or {}
    upload_id = data.get('uploadId')
    key = data.get('key')

    if not all([upload_id, key]):
        return jsonify({'status': 'error', 'message': 'Missing uploadId or key.'}), 400

    try:
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
        
        s3.abort_multipart_upload(
            Bucket=bucket_name,
            Key=key,
            UploadId=upload_id
        )
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/presign-upload', methods=['POST'])
@login_required
def presign_upload(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
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
            used = _get_user_storage_used(quota_owner)
            limit = quota_owner.quota_limit or 2147483648
            
            if used + filesize > limit:
                return jsonify({
                    'status': 'error', 
                    'message': f'Storage quota exceeded. Available: {round((limit - used)/1048576, 1)}MB.'
                }), 400

        filename_secured = secure_filename(filename)
        key = prefix + filename_secured
        
        endpoint_url = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
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
            'url': _fix_s3_url(presigned['url']),
            'fields': presigned['fields'],
            'key': key
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/confirm-upload', methods=['POST'])
@login_required
def confirm_upload(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    key = data.get('key')

    if not key:
        return jsonify({'status': 'error', 'message': 'Missing key.'}), 400

    try:
        s3 = _get_s3_client(conn)
        actual_size = 0
        try:
            response = s3.head_object(Bucket=bucket_name, Key=key)
            actual_size = response.get('ContentLength', 0)
        except Exception as e:
            print(f"Warning: Failed to get object size via HeadObject: {e}")
            
        filename = key.split('/')[-1]
        quota_owner_id = owner_id if owner_id else g.user.id
        
        _log_action(
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
        return jsonify({'status': 'success', 'size': actual_size})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/create-folder', methods=['POST'])
@login_required
def create_folder(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
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
        s3 = _get_s3_client(conn)
        s3.put_object(Bucket=bucket_name, Key=key, Body=b'')
        _log_action(g.user.id, None, conn.name, bucket_name, 'CREATE_FOLDER', f"Created folder '{key}'")
        return jsonify({'status': 'success', 'message': 'Folder created successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/save-text', methods=['POST'])
@login_required
def save_text_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
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
        s3 = _get_s3_client(conn, endpoint_url=endpoint_url)
        
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
                used = _get_user_storage_used(quota_owner)
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
        
        _log_action(
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

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/rename', methods=['POST'])
@login_required
def rename_object(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    data = request.get_json() or {}
    old_key = data.get('old_key')
    new_name = data.get('new_name')
    
    if not access_control_check_file_edit_access(g.user, conn, bucket_name, old_key):
        return jsonify({'status': 'error', 'message': 'Permission Denied. Báº¡n chá»‰ cÃ³ thá»ƒ sá»­a file/thÆ° má»¥c do chÃ­nh mÃ¬nh táº£i lÃªn.'}), 403
    
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
        s3 = _get_s3_client(conn)
        
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
                    
            _log_action(g.user.id, None, conn.name, bucket_name, 'RENAME_FOLDER', f"Renamed folder {old_key} to {new_key}")
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
            else:
                new_uf = UploadedFile(
                    connection_id=conn.id,
                    bucket_name=bucket_name,
                    file_key=new_key,
                    user_id=g.user.id if g.user else 1
                )
                db.session.add(new_uf)
                
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
            
            _log_action(g.user.id, None, conn.name, bucket_name, 'RENAME_FILE', f"Renamed file {old_key} to {new_key}")
            
        return jsonify({'status': 'success', 'message': 'Renamed successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/delete-object', methods=['POST'])
@login_required
def delete_object(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.form.get('key')
    prefix = request.form.get('prefix', '')
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        flash('Permission Denied.', 'error')
        return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))
        
    if not key:
        flash('No object key specified.', 'error')
        return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))

    if not access_control_check_file_edit_access(g.user, conn, bucket_name, key):
        flash('Permission Denied. Báº¡n chá»‰ cÃ³ thá»ƒ xÃ³a file/thÆ° má»¥c do chÃ­nh mÃ¬nh táº£i lÃªn.', 'error')
        return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    try:
        s3 = _get_s3_client(conn)
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
        _log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_FILE', details)
        
        flash('File deleted successfully.', 'success')
    except Exception as e:
        flash(f'Failed to delete file: {str(e)}', 'error')

    return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name, prefix=prefix))

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/delete-objects-bulk', methods=['POST'])
@login_required
def delete_objects_bulk(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    if not access_control_check_bucket_edit_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied.'}), 403
        
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    owner_id = mapping.user_id if mapping else None

    data = request.get_json() or {}
    keys = data.get('keys', [])
    
    if not keys:
        return jsonify({'status': 'error', 'message': 'No object keys specified.'}), 400

    for key in keys:
        if not access_control_check_file_edit_access(g.user, conn, bucket_name, key):
            return jsonify({'status': 'error', 'message': 'Permission Denied. Báº¡n chá»‰ cÃ³ thá»ƒ xÃ³a nhá»¯ng file do chÃ­nh mÃ¬nh táº£i lÃªn.'}), 403

    try:
        s3 = _get_s3_client(conn)
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
        _log_action(g.user.id, owner_id, conn.name, bucket_name, 'DELETE_FILE', details)
        
        return jsonify({'status': 'success', 'message': f'Deleted {count} object(s).'})
    except Exception as e:
        current_app.logger.error(f"Bulk delete failed for bucket {bucket_name}: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Failed to delete objects: {str(e)}'}), 500

@bp.route('/connection/<connection_id>/bucket/<bucket_name>/download-zip', methods=['POST'])
def download_zip(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    
    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']
    
    if not is_public and g.user is None:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        
    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return jsonify({'status': 'error', 'message': 'Permission Denied'}), 403

    # Parse items
    data = request.get_json() or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'status': 'error', 'message': 'No items selected'}), 400

    try:
        s3 = _get_s3_client(conn)
        all_files = []
        
        for item in items:
            key = item.get('key')
            item_type = item.get('type', 'file')
            
            if item_type == 'file':
                all_files.append((key, key))
            else:
                prefix = key if key.endswith('/') else key + '/'
                paginator = s3.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
                
                parts = prefix.rstrip('/').split('/')
                if len(parts) > 1:
                    parent_prefix = '/'.join(parts[:-1]) + '/'
                else:
                    parent_prefix = ''
                    
                for page in pages:
                    for obj in page.get('Contents', []):
                        obj_key = obj.get('Key')
                        if obj_key == prefix:
                            continue
                        rel_path = obj_key[len(parent_prefix):] if obj_key.startswith(parent_prefix) else obj_key
                        all_files.append((obj_key, rel_path))

        if not all_files:
            return jsonify({'status': 'error', 'message': 'No files found to download'}), 404

        class ZipStreamWriter:
            def __init__(self):
                self.buffer = io.BytesIO()
                self.offset = 0

            def write(self, data):
                self.buffer.write(data)
                self.offset += len(data)
                return len(data)

            def tell(self):
                return self.offset

            def flush(self):
                self.buffer.flush()

            def get_data(self):
                val = self.buffer.getvalue()
                self.buffer.seek(0)
                self.buffer.truncate(0)
                return val

        def generate_zip():
            import zipfile
            stream = ZipStreamWriter()
            with zipfile.ZipFile(stream, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                for obj_key, rel_path in all_files:
                    try:
                        response = s3.get_object(Bucket=bucket_name, Key=obj_key)
                        body = response['Body']
                        
                        zinfo = zipfile.ZipInfo(rel_path)
                        if 'LastModified' in response:
                            dt = response['LastModified']
                            zinfo.date_time = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
                        
                        with zf.open(zinfo, mode='w') as dest_file:
                            for chunk in body.iter_chunks(chunk_size=1024*64):
                                dest_file.write(chunk)
                                data = stream.get_data()
                                if data:
                                    yield data
                    except Exception as e:
                        current_app.logger.error(f"Error zipping file {obj_key}: {e}")
                        try:
                            zf.writestr(f"error-{obj_key.replace('/', '-')}.txt", f"Failed to download {obj_key}: {str(e)}")
                        except Exception:
                            pass
                        data = stream.get_data()
                        if data:
                            yield data
            data = stream.get_data()
            if data:
                yield data

        filename = f"download-{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
        headers = {
            'Content-Type': 'application/zip',
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-cache',
        }
        return Response(stream_with_context(generate_zip()), headers=headers)
        
    except Exception as e:
        current_app.logger.error(f"Error creating zip download: {e}\n{traceback.format_exc()}")
        return jsonify({'status': 'error', 'message': f'Internal Server Error: {str(e)}'}), 500
