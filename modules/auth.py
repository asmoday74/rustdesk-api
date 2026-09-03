import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, session, redirect, url_for

from modules.database import execute_query, get_user_by_username

SESSION_TIMEOUT_HOURS = 2

def hash_password(password, salt=None):
    if not salt:
        salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return salt + ':' + hash_obj.hex()

def verify_password(stored_password, provided_password):
    try:
        salt, stored_hash = stored_password.split(':')
        hash_obj = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 100000)
        return hash_obj.hex() == stored_hash
    except:
        return False

def update_last_activity():
    if 'user_id' in session:
        session['last_activity'] = datetime.now().isoformat()

def is_session_expired():
    if 'last_activity' not in session:
        return True
    try:
        last_activity = datetime.fromisoformat(session['last_activity'])
        timeout_delta = timedelta(hours=SESSION_TIMEOUT_HOURS)
        if datetime.now() - last_activity > timeout_delta:
            return True
    except:
        return True
    return False

def get_session_timeout_seconds():
    return SESSION_TIMEOUT_HOURS * 3600

def add_audit_log(user_id, action, target, details, ip):
    try:
        execute_query("""
            INSERT INTO audit_log (user_id, action, target, details, ip)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, action, target, details, ip))
    except Exception as e:
        pass

def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page'))
        
        if is_session_expired():
            user_id = session.get('user_id')
            username = session.get('username', 'Unknown')
            add_audit_log(user_id, 'SESSION_TIMEOUT', username, f'Auto logout after {SESSION_TIMEOUT_HOURS} hours of inactivity', request.remote_addr)
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Session expired due to inactivity'}), 401
            return redirect(url_for('login_page'))
        
        update_last_activity()
        return f(*args, **kwargs)
    return decorated_function

def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') == 'admin':
            return f(*args, **kwargs)
        
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Admin rights required'}), 403
        return redirect(url_for('login_page'))
    return decorated_function

def get_all_users():
    from modules.database import execute_query
    return execute_query('SELECT id, username, role, email, group_id, auth_source, created_at, last_login FROM users', fetch_all=True)

def create_user(username, password, role='user', email=None, group_id=1):
    from modules.database import execute_query, get_user_by_username
    existing = get_user_by_username(username)
    if existing:
        return False, 'Username already exists'

    password_hash = hash_password(password)
    execute_query("""
        INSERT INTO users (username, password_hash, role, email, group_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (username, password_hash, role, email, group_id or 1))
    if role == 'admin':
        from modules import groups
        created = get_user_by_username(username)
        if created:
            groups.add_admin_membership(created['id'])
    return True, 'User created'

def delete_user(user_id):
    from modules.database import execute_query
    admin_count = execute_query('SELECT COUNT(*) as count FROM users WHERE role = %s', ('admin',), fetch_one=True)
    user = execute_query('SELECT role FROM users WHERE id = %s', (user_id,), fetch_one=True)
    
    if user and user.get('role') == 'admin' and admin_count and admin_count.get('count', 0) <= 1:
        return False, 'Cannot delete the last admin user'
    
    execute_query('DELETE FROM users WHERE id = %s', (user_id,))
    return True, 'User deleted'

def update_user_last_login(user_id):
    from modules.database import execute_query
    execute_query('UPDATE users SET last_login = NOW() WHERE id = %s', (user_id,))