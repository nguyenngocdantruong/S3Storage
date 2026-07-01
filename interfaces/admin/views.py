from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from extensions import db
from infrastructure.persistence.models import AuditLog, BucketAccess, S3Connection, User
from interfaces.middleware.context import admin_required, login_required
from use_cases.audit import log_action as audit_log_action
from use_cases.quota import get_user_storage_used as quota_get_user_storage_used


bp = Blueprint('admin', __name__)


def _get_s3_client(connection, endpoint_url=None):
    return current_app.config['GET_S3_CLIENT'](connection, endpoint_url=endpoint_url)


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


@bp.route('/admin/users')
@admin_required
def manage_users():
    users = User.query.order_by(User.created_at.desc()).all()
    user_stats = []
    for user in users:
        used = quota_get_user_storage_used(user, db_session=db.session, storage_provider_factory=_get_s3_client)
        user_stats.append({
            'user': user,
            'storage_used': used,
            'quota_limit': user.quota_limit or 2147483648,
        })
    return render_template('users.html', user_stats=user_stats)


@bp.route('/admin/user/<int:user_id>/quota', methods=['POST'])
@admin_required
def update_user_quota(user_id):
    user = db.get_or_404(User, user_id)
    quota_gb = request.form.get('quota_gb', type=float)
    if quota_gb is None or quota_gb <= 0:
        flash('Invalid quota value.', 'error')
        return redirect(url_for('admin.manage_users'))

    user.quota_limit = int(quota_gb * 1024 * 1024 * 1024)
    db.session.commit()
    flash(f'Quota for {user.name} updated to {quota_gb} GB.', 'success')
    return redirect(url_for('admin.manage_users'))


@bp.route('/admin/user/<int:user_id>/toggle-status', methods=['POST'])
@admin_required
def toggle_user_status(user_id):
    user = db.get_or_404(User, user_id)
    if user.role == 'Admin':
        flash('Cannot disable the Admin account.', 'error')
        return redirect(url_for('admin.manage_users'))

    user.is_active = not getattr(user, 'is_active', True)
    db.session.commit()
    status_text = 'activated' if user.is_active else 'disabled'
    flash(f'User {user.name} was {status_text}.', 'success')
    return redirect(url_for('admin.manage_users'))


@bp.route('/admin/user/<int:user_id>/update-role', methods=['POST'])
@admin_required
def update_user_role(user_id):
    user = db.get_or_404(User, user_id)
    new_role = request.form.get('role', '').strip()
    if new_role not in ['Admin', 'User']:
        flash('Invalid role.', 'error')
        return redirect(url_for('admin.manage_users'))

    user.role = new_role
    db.session.commit()
    flash(f'Updated role for {user.name} to {new_role}.', 'success')
    return redirect(url_for('admin.manage_users'))


@bp.route('/admin/functions')
@admin_required
def admin_functions():
    return render_template('admin_functions.html')


@bp.route('/admin/bucket-access')
@admin_required
def bucket_access_list():
    access_list = BucketAccess.query.order_by(BucketAccess.created_at.desc()).all()
    users = User.query.order_by(User.name.asc()).all()
    connections = S3Connection.query.order_by(S3Connection.name.asc()).all()
    return render_template('bucket_access.html', access_list=access_list, users=users, connections=connections)


@bp.route('/admin/bucket-access/grant', methods=['POST'])
@admin_required
def bucket_access_grant():
    user_id = request.form.get('user_id', type=int)
    connection_id = request.form.get('connection_id', type=int)
    bucket_name = request.form.get('bucket_name', '').strip()
    role = request.form.get('role', '').strip() or 'Viewer'

    user = db.session.get(User, user_id) if user_id else None
    conn = db.session.get(S3Connection, connection_id) if connection_id else None
    if not user or not conn or not bucket_name:
        flash('Missing bucket access information.', 'error')
        return redirect(url_for('admin.bucket_access_list'))

    exists = BucketAccess.query.filter_by(user_id=user.id, connection_id=conn.id, bucket_name=bucket_name).first()
    if exists:
        flash('Bucket access already exists.', 'error')
        return redirect(url_for('admin.bucket_access_list'))

    access = BucketAccess(user_id=user.id, connection_id=conn.id, bucket_name=bucket_name, role=role)
    db.session.add(access)
    db.session.commit()
    flash('Bucket access granted.', 'success')
    return redirect(url_for('admin.bucket_access_list'))


@bp.route('/admin/bucket-access/<int:access_id>/revoke', methods=['POST'])
@admin_required
def bucket_access_revoke(access_id):
    access = db.get_or_404(BucketAccess, access_id)
    db.session.delete(access)
    db.session.commit()
    flash('Bucket access revoked.', 'success')
    return redirect(url_for('admin.bucket_access_list'))


@bp.route('/logs')
@login_required
def view_logs():
    page = request.args.get('page', 1, type=int)
    pagination = AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('logs.html', pagination=pagination, logs=pagination.items)


@bp.route('/admin/system-logs')
@admin_required
def view_system_logs():
    page = request.args.get('page', 1, type=int)
    action = request.args.get('action', '').strip().upper()
    query = request.args.get('q', '').strip()

    logs_query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    if action:
        logs_query = logs_query.filter(AuditLog.action_type == action)
    if query:
        like_query = f'%{query}%'
        logs_query = logs_query.outerjoin(AuditLog.actor).filter(
            or_(
                AuditLog.action_type.ilike(like_query),
                AuditLog.details.ilike(like_query),
                AuditLog.connection_name.ilike(like_query),
                AuditLog.bucket_name.ilike(like_query),
                User.name.ilike(like_query),
                User.email.ilike(like_query),
            )
        )

    pagination = logs_query.paginate(page=page, per_page=25, error_out=False)
    available_actions = [
        row[0]
        for row in db.session.query(AuditLog.action_type)
        .filter(AuditLog.action_type.isnot(None))
        .distinct()
        .order_by(AuditLog.action_type.asc())
        .all()
    ]
    return render_template(
        'system_logs.html',
        logs=pagination.items,
        pagination=pagination,
        selected_action=action,
        query=query,
        available_actions=available_actions,
    )


@bp.route('/admin/system-logs/clear', methods=['POST'])
@admin_required
def clear_system_logs():
    AuditLog.query.delete()
    db.session.commit()
    flash('Audit activity logs cleared.', 'success')
    return redirect(url_for('admin.view_system_logs'))
