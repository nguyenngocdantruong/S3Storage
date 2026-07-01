from datetime import datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

from extensions import db
from infrastructure.persistence.models import AuditLog, BucketAccess, S3Connection, S3FileIndex, User, UserBucket


bp = Blueprint('main', __name__)


@bp.route('/')
def dashboard():
    connections = S3Connection.query.order_by(S3Connection.created_at.desc()).all()

    stats_data = {}
    if g.user and g.user.role == 'Admin':
        try:
            total_users = User.query.count()
            total_connections = S3Connection.query.count()

            aws_count = 0
            compat_count = 0
            for connection in connections:
                if connection.endpoint_url and 'amazonaws.com' in connection.endpoint_url.lower():
                    aws_count += 1
                elif not connection.endpoint_url:
                    aws_count += 1
                else:
                    compat_count += 1

            action_stats = db.session.query(AuditLog.action_type, func.count(AuditLog.id)).group_by(AuditLog.action_type).all()
            action_labels = [item[0] or 'OTHER' for item in action_stats]
            action_counts = [item[1] for item in action_stats]

            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            daily_activity = db.session.query(func.date(AuditLog.timestamp), func.count(AuditLog.id)).filter(AuditLog.timestamp >= seven_days_ago).group_by(func.date(AuditLog.timestamp)).all()

            activity_trend = {}
            for i in range(7):
                date_value = (datetime.utcnow() - timedelta(days=i)).date()
                activity_trend[date_value.strftime('%Y-%m-%d')] = 0
            for date_value, count in daily_activity:
                date_str = str(date_value) if date_value else ''
                if date_str in activity_trend:
                    activity_trend[date_str] = count

            sorted_trend = sorted(activity_trend.items())
            stats_data = {
                'total_users': total_users,
                'total_connections': total_connections,
                'connection_dist': {'AWS': aws_count, 'Compatible': compat_count},
                'action_stats': {'labels': action_labels, 'counts': action_counts},
                'activity_trend': {
                    'labels': [item[0] for item in sorted_trend],
                    'counts': [item[1] for item in sorted_trend],
                },
            }
        except Exception as exc:
            current_app.logger.error(f'Error gathering stats: {exc}')

    return render_template('dashboard.html', connections=connections, stats_data=stats_data)


@bp.route('/search')
def global_search():
    if g.user is None:
        return redirect(url_for('auth.login'))

    query = request.args.get('q', '').strip()
    results = []

    if query:
        owned_conn_ids = [conn.id for conn in g.user.owned_connections]
        mapped_buckets = UserBucket.query.filter_by(user_id=g.user.id).all()
        shared_access = BucketAccess.query.filter_by(user_id=g.user.id).all()

        allowed = set()
        for mapped_bucket in mapped_buckets:
            allowed.add((mapped_bucket.connection_id, mapped_bucket.bucket_name))
        for shared_bucket in shared_access:
            allowed.add((shared_bucket.connection_id, shared_bucket.bucket_name))

        public_mappings = UserBucket.query.filter(UserBucket.access_type.in_(['public', 'public_view', 'public_edit', 'public_upload'])).all()
        for public_mapping in public_mappings:
            allowed.add((public_mapping.connection_id, public_mapping.bucket_name))

        if g.user.role == 'Admin':
            results = S3FileIndex.query.filter(S3FileIndex.file_name.like(f'%{query}%')).limit(100).all()
        else:
            raw_matches = S3FileIndex.query.filter(S3FileIndex.file_name.like(f'%{query}%')).limit(500).all()
            for item in raw_matches:
                if item.connection_id in owned_conn_ids or (item.connection_id, item.bucket_name) in allowed:
                    results.append(item)
                    if len(results) >= 100:
                        break

    if g.user.role == 'Admin':
        total_indexed = S3FileIndex.query.count()
    else:
        owned_conn_ids = [conn.id for conn in g.user.owned_connections]
        total_indexed = S3FileIndex.query.filter(S3FileIndex.connection_id.in_(owned_conn_ids) if owned_conn_ids else False).count()

    return render_template('search.html', query=query, results=results, total_indexed=total_indexed)


@bp.route('/search/sync', methods=['POST'])
def sync_search_index():
    if g.user is None:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401

    if g.user.role == 'Admin':
        connections = S3Connection.query.all()
    else:
        connections = g.user.owned_connections

    try:
        synced_count = 0
        for conn in connections:
            s3 = current_app.config['GET_S3_CLIENT'](conn)
            try:
                if g.user.role == 'Admin':
                    response = s3.list_buckets()
                    buckets = [bucket['Name'] for bucket in response.get('Buckets', [])]
                else:
                    user_buckets = UserBucket.query.filter_by(user_id=g.user.id, connection_id=conn.id).all()
                    buckets = [user_bucket.bucket_name for user_bucket in user_buckets]

                S3FileIndex.query.filter_by(connection_id=conn.id).delete()

                for bucket in buckets:
                    try:
                        paginator = s3.get_paginator('list_objects_v2')
                        page_iterator = paginator.paginate(Bucket=bucket, PaginationConfig={'MaxItems': 5000})
                        for page in page_iterator:
                            for obj in page.get('Contents', []):
                                key = obj['Key']
                                if key.endswith('/') or key == '':
                                    continue

                                db.session.add(S3FileIndex(
                                    connection_id=conn.id,
                                    bucket_name=bucket,
                                    file_key=key,
                                    file_name=key.split('/')[-1] or key,
                                    size=obj.get('Size', 0),
                                    last_modified=obj.get('LastModified'),
                                ))
                                synced_count += 1
                    except Exception as exc:
                        current_app.logger.error(f'Failed to index bucket {bucket}: {exc}')
            except Exception as exc:
                current_app.logger.error(f'Failed to list buckets for connection {conn.name}: {exc}')

        db.session.commit()
        return jsonify({'status': 'success', 'message': f'Search index updated successfully! Scanned {synced_count} files.'})
    except Exception as exc:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Index sync error: {str(exc)}'}), 500
