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
    """Пользователи для админ-панели: данные + прямые членства в группах"""
    from modules.database import execute_query
    users = execute_query(
        'SELECT id, username, nickname, email, group_id, auth_source, created_at, last_login FROM users',
        fetch_all=True) or []
    memberships = execute_query("""
        SELECT gm.member_id AS user_id, g.id AS group_id, g.name AS group_name,
               g.source AS source, g.builtin AS builtin
        FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = 'user'
    """, fetch_all=True) or []
    by_user = {}
    for m in memberships:
        by_user.setdefault(m['user_id'], []).append(m)
    for u in users:
        ms = by_user.get(u['id'], [])
        u['groups'] = sorted(m['group_name'] for m in ms)
        u['memberships'] = [
            {'group_id': m['group_id'], 'name': m['group_name'], 'source': m['source'] or 'local'}
            for m in sorted(ms, key=lambda x: x['group_name'].lower())
        ]
        u['is_admin'] = any(m['builtin'] == 2 for m in ms)
    return users

def create_user(username, password, email=None, group_id=1, nickname=''):
    from modules.database import execute_query
    # '@' и '\\' в локальном логине запрещены: такие форматы зарезервированы
    # для доменного входа (user@domain / DOMAIN\user).
    if not username or '@' in username or '\\' in username:
        return False, 'Username must not contain @ or \\'
    # Уникальность гарантируется среди локальных пользователей; доменный
    # пользователь с тем же именем может существовать параллельно.
    existing = execute_query("""
        SELECT id FROM users WHERE username = %s AND auth_source = 'local'
    """, (username,), fetch_one=True)
    if existing:
        return False, 'Username already exists'

    password_hash = hash_password(password)
    execute_query("""
        INSERT INTO users (username, password_hash, email, group_id, nickname)
        VALUES (%s, %s, %s, %s, %s)
    """, (username, password_hash, email, group_id or 1, nickname or ''))
    return True, 'User created'

def delete_user(user_id):
    from modules.database import execute_query
    from modules import groups
    user = execute_query('SELECT id FROM users WHERE id = %s', (user_id,), fetch_one=True)
    if user and groups.is_last_admin_user(user_id):
        return False, 'Cannot delete the last administrator'

    execute_query('DELETE FROM users WHERE id = %s', (user_id,))
    groups.remove_user_memberships(user_id)
    return True, 'User deleted'

def update_user_last_login(user_id):
    from modules.database import execute_query
    execute_query('UPDATE users SET last_login = NOW() WHERE id = %s', (user_id,))