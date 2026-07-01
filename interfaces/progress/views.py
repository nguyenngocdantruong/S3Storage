from datetime import datetime

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from extensions import db
from infrastructure.persistence.models import ItemLike, S3Connection, VideoProgress
from interfaces.middleware.context import login_required


bp = Blueprint('progress', __name__)


@bp.route('/video/progress', methods=['POST'])
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

@bp.route('/api/like', methods=['POST'])
def like_item():
    data = request.get_json() or {}
    connection_name = data.get('connection_name')
    bucket_name = data.get('bucket_name')
    file_key = data.get('file_key')
    count = data.get('count', 1)

    if not all([connection_name, bucket_name, file_key]):
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400

    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1

    like_record = ItemLike.query.filter_by(
        connection_name=connection_name,
        bucket_name=bucket_name,
        file_key=file_key
    ).first()

    if not like_record:
        like_record = ItemLike(
            connection_name=connection_name,
            bucket_name=bucket_name,
            file_key=file_key,
            like_count=0
        )
        db.session.add(like_record)

    like_record.like_count += count
    db.session.commit()

    return jsonify({'status': 'success', 'like_count': like_record.like_count})

@bp.route('/progress')
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

@bp.route('/progress/delete-item/<int:progress_id>', methods=['POST'])
@login_required
def delete_progress_item(progress_id):
    progress = db.get_or_404(VideoProgress, progress_id)
    if progress.user_id != g.user.id:
        flash('Permission Denied.', 'error')
        return redirect(url_for('progress.list_progress'))
        
    db.session.delete(progress)
    db.session.commit()
    flash(f"Deleted progress for file: {progress.file_name}", 'success')
    return redirect(url_for('progress.list_progress'))

@bp.route('/progress/delete-bucket/<bucket_name>', methods=['POST'])
@login_required
def delete_progress_bucket(bucket_name):
    VideoProgress.query.filter_by(user_id=g.user.id, bucket_name=bucket_name).delete()
    db.session.commit()
    flash(f"Deleted all progress records for bucket: {bucket_name}", 'success')
    return redirect(url_for('progress.list_progress'))

