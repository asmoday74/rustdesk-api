from flask import request, jsonify, session
from datetime import datetime

from modules.auth import (
    verify_password, add_audit_log, get_user_by_username,
    update_user_last_login, update_last_activity, get_session_timeout_seconds,
    require_admin, hash_password
)
from modules.database import execute_query, get_user_by_username as get_user


def provision_ldap_user(info):
    """Создаёт/обновляет локальную запись доменного пользователя.
    Возвращает пользователя или None при конфликте с локальной записью."""
    from modules import groups as gr
    username = info.get('username') or ''
    if not username:
        return None
    user = get_user(username)
    if user:
        if user.get('auth_source', 'local') != 'ldap':
            return None
        if info.get('dn') and info['dn'] != user.get('ldap_dn'):
            execute_query('UPDATE users SET ldap_dn = %s WHERE id = %s',
                          (info['dn'], user['id']))
    else:
        import secrets
        users_group = gr.get_builtin_group(gr.BUILTIN_USERS)
        primary_gid = users_group['id'] if users_group else 1
        execute_query("""
            INSERT INTO users (username, password_hash, role, email, group_id,
                               auth_source, ldap_dn, nickname)
            VALUES (%s, %s, 'user', %s, %s, 'ldap', %s, %s)
        """, (username, secrets.token_hex(32), info.get('email') or None,
              primary_gid, info.get('dn') or '', info.get('display_name') or ''))
        user = get_user(username)
        if user and users_group:
            gr.add_member(users_group['id'], gr.MEMBER_USER, user['id'])
    if user:
        gr.sync_ad_memberships(user['id'], info.get('group_sids') or [])
    return user


def init_auth_routes(app):
    
    @app.route('/api/login', methods=['POST'])
    def login():
        try:
            data = request.get_json()
            username = (data.get('username') or '').strip()
            password = data.get('password') or ''
            from modules import ldap_auth

            if ldap_auth.parse_domain_login(username):
                # Доменный вход: только через LDAP
                if not ldap_auth.is_enabled():
                    return jsonify({'error': 'LDAP authentication is not configured'}), 401
                info = ldap_auth.authenticate(username, password)
                if not info:
                    return jsonify({'error': 'Invalid credentials'}), 401
                user = provision_ldap_user(info)
                if not user:
                    return jsonify({'error': 'Invalid credentials'}), 401
            else:
                # Локальный вход
                user = get_user(username)
                if user and user.get('auth_source', 'local') != 'local':
                    return jsonify({'error': 'Invalid credentials'}), 401
                if not (user and verify_password(user.get('password_hash'), password)):
                    return jsonify({'error': 'Invalid credentials'}), 401

            # Режим клиента RustDesk: запрос содержит uuid/deviceInfo.
            # Совместимость с lejianwen/rustdesk-api - выдаем access_token
            if data.get('uuid') or data.get('deviceInfo'):
                from modules import ab
                if not ab.is_user_enabled(user):
                    return jsonify({'error': 'UserDisabled'}), 401
                token = ab.create_user_token(user, data.get('id'), data.get('uuid'))
                # Привязываем устройство к владельцу для вкладки "Группа"
                ab.bind_device_user(data.get('uuid'), user)
                update_user_last_login(user['id'])
                add_audit_log(user['id'], 'CLIENT_LOGIN', username,
                              f"RustDesk client login (device: {data.get('id')})", request.remote_addr)
                return jsonify({
                    'access_token': token,
                    'type': 'access_token',
                    'user': ab.user_payload(user)
                }), 200

            session['user_id'] = user['id']
            session['username'] = user['username']
            from modules import groups as gr
            session['role'] = 'admin' if gr.is_admin_user(user) else (user.get('role') or 'user')
            session['login_time'] = datetime.now().isoformat()
            session['last_activity'] = datetime.now().isoformat()

            update_user_last_login(user['id'])
            add_audit_log(user['id'], 'LOGIN', username, 'Successful login', request.remote_addr)

            return jsonify({
                'status': 'success',
                'role': session['role'],
                'session_timeout_hours': get_session_timeout_seconds() / 3600
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/auth/logout', methods=['POST'])
    def api_logout():
        user_id = session.get('user_id')
        username = session.get('username', 'Unknown')
        if user_id:
            add_audit_log(user_id, 'LOGOUT', username, 'Manual logout via API', request.remote_addr)
        session.clear()
        return jsonify({'status': 'success'}), 200
    
    @app.route('/api/session/check', methods=['GET'])
    def check_session():
        from modules.auth import is_session_expired
        
        if 'user_id' not in session:
            return jsonify({'active': False, 'reason': 'not_authenticated'}), 200
        
        if is_session_expired():
            return jsonify({'active': False, 'reason': 'timeout'}), 200
        
        update_last_activity()
        return jsonify({
            'active': True,
            'user': session.get('username'),
            'role': session.get('role'),
            'timeout_hours': get_session_timeout_seconds() / 3600
        }), 200
    
    @app.route('/api/session/extend', methods=['POST'])
    def extend_session():
        if 'user_id' not in session:
            return jsonify({'error': 'No active session'}), 401
        
        update_last_activity()
        return jsonify({
            'status': 'success',
            'last_activity': session.get('last_activity')
        }), 200
    
    @app.route('/api/users/me', methods=['GET'])
    def get_current_user():
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        
        return jsonify({
            'id': session.get('user_id'),
            'username': session.get('username'),
            'role': session.get('role')
        })
    
    @app.route('/api/users', methods=['GET'])
    def get_users():
        # Режим клиента RustDesk (Bearer-токен): формат lejianwen/rustdesk-api
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            from modules import ab
            user, ut = ab.get_user_by_access_token(auth_header[7:].strip())
            if not user or not ab.is_user_enabled(user):
                return jsonify({'error': 'Unauthorized'}), 401
            ab.auto_refresh_token(ut)
            if ab.can_see_group_members(user):
                users = ab.list_users_by_group(user.get('group_id', 1))
            else:
                users = [user]
            return jsonify({
                'total': len(users),
                'data': [ab.user_payload(u) for u in users]
            })

        from modules.auth import get_all_users, require_admin
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        return jsonify(get_all_users())
    
    @app.route('/api/users', methods=['POST'])
    def add_user():
        from modules.auth import create_user, require_admin, add_audit_log
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        data = request.get_json()
        success, message = create_user(data.get('username'), data.get('password'),
                                       data.get('role', 'user'), data.get('email'),
                                       data.get('group_id', 1))
        if success:
            add_audit_log(session.get('user_id'), 'CREATE_USER', data.get('username'), message, request.remote_addr)
            return jsonify({'message': message}), 201
        return jsonify({'error': message}), 400
    
    @app.route('/api/users/<int:user_id>', methods=['DELETE'])
    def remove_user(user_id):
        from modules.auth import delete_user, require_admin, add_audit_log
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        success, message = delete_user(user_id)
        if success:
            add_audit_log(session.get('user_id'), 'DELETE_USER', str(user_id), message, request.remote_addr)
            return jsonify({'message': message}), 200
        return jsonify({'error': message}), 400

    # ========== НОВЫЙ ЭНДПОИНТ ДЛЯ СМЕНЫ ПАРОЛЯ ==========
    @app.route('/api/users/<int:user_id>/password', methods=['PUT'])
    def change_user_password(user_id):
        """Смена пароля пользователя (только для администратора)"""
        from modules.auth import require_admin, add_audit_log
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        try:
            data = request.get_json()
            new_password = data.get('new_password')
            
            if not new_password or len(new_password) < 4:
                return jsonify({'error': 'Password must be at least 4 characters'}), 400
            
            # Проверяем, существует ли пользователь
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({'error': 'User not found'}), 404
            
            # Для доменных пользователей пароль управляется каталогом
            if user.get('auth_source', 'local') != 'local':
                return jsonify({'error': 'Password is managed by the domain'}), 403
            
            # Хешируем новый пароль
            new_password_hash = hash_password(new_password)
            
            # Обновляем пароль в БД
            execute_query(
                'UPDATE users SET password_hash = %s WHERE id = %s',
                (new_password_hash, user_id)
            )
            
            # Логируем действие
            admin_username = session.get('username', 'Unknown')
            add_audit_log(
                session.get('user_id'), 
                'CHANGE_PASSWORD', 
                user.get('username'), 
                f'Password changed by admin {admin_username}', 
                request.remote_addr
            )
            
            return jsonify({
                'status': 'success',
                'message': f'Password for user "{user.get("username")}" has been changed'
            }), 200
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Вспомогательная функция для получения пользователя по ID
    def get_user_by_id(user_id):
        return execute_query('SELECT * FROM users WHERE id = %s', (user_id,), fetch_one=True)