from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from extensions import db
from infrastructure.persistence.models import User
from interfaces.middleware.context import login_required
from use_cases.audit import log_action as audit_log_action


bp = Blueprint('auth', __name__)


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



@bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('main.dashboard'))
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
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if not getattr(user, 'is_active', True):
                flash('T?i kho?n c?a b?n ?? b? v? hi?u h?a b?i qu?n tr? vi?n.', 'error')
                return render_template('login.html')
            session.clear()
            session['user_id'] = user.id
            session.permanent = bool(remember)
            _log_action(user.id, user.id, None, None, 'LOGIN', f"User '{user.email}' logged in")
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(url_for('main.dashboard'))

        flash('Invalid email or password.', 'error')

    return render_template('login.html')


@bp.route('/logout')
def logout():
    if g.user:
        _log_action(g.user.id, g.user.id, None, None, 'LOGOUT', f"User '{g.user.email}' logged out")
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@bp.route('/profile', methods=['GET', 'POST'])
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
        return redirect(url_for('auth.profile'))

    return render_template('profile.html')
