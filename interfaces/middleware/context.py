from functools import wraps

from flask import flash, g, redirect, session, url_for

from extensions import db
from infrastructure.persistence.models import User
from use_cases.quota import get_user_storage_used as quota_get_user_storage_used


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


def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is None:
        g.user = None
    else:
        g.user = db.session.get(User, user_id)
        if g.user and not getattr(g.user, 'is_active', True):
            session.clear()
            g.user = None
            flash('T?i kho?n c?a b?n ?? b? v? hi?u h?a b?i qu?n tr? vi?n.', 'error')


def build_quota_injector(storage_provider_factory):
    def inject_quota():
        if g.user:
            if g.user.role == 'Admin':
                return {
                    'quota_used': 0,
                    'quota_limit': 0,
                    'quota_pct': 0,
                    'quota_is_unlimited': True,
                }
            used = quota_get_user_storage_used(g.user, db_session=db.session, storage_provider_factory=storage_provider_factory)
            limit = g.user.quota_limit or 2147483648
            pct = round(used / limit * 100, 1) if limit > 0 else 0
            return {
                'quota_used': used,
                'quota_limit': limit,
                'quota_pct': pct,
                'quota_is_unlimited': False,
            }
        return {
            'quota_used': 0,
            'quota_limit': 0,
            'quota_pct': 0,
            'quota_is_unlimited': True,
        }

    return inject_quota


def inject_g():
    return {'g': TemplateG(g)}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('auth.login'))
        if g.user.role != 'Admin':
            flash('Admin permissions required for this action.', 'error')
            return redirect(url_for('main.dashboard'))
        return view(*args, **kwargs)

    return wrapped_view


def register_context(app, storage_provider_factory):
    app.before_request(load_logged_in_user)
    app.context_processor(build_quota_injector(storage_provider_factory))
    app.context_processor(inject_g)
