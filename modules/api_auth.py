from flask import request, jsonify, session
from datetime import datetime

from modules.auth import (
    verify_password, add_audit_log, get_user_by_username,
    update_user_last_login, update_last_activity, get_session_timeout_seconds,
    require_admin, hash_password
)
from modules.database import execute_query, get_user_by_username as get_user


def sync_user_ad_groups(user, info):
    """Синхронизирует ВСЕ AD-группы пользователя при входе.

    Источники:
    - memberOf — прямые группы пользователя; неизвестные системе группы
      создаются автоматически (source='ad', имя/DN/SID из каталога);
    - tokenGroups — SID известных системе групп (поддерживает вложенность
      для групп, добавленных вручную).
    Членства в AD-группах приводятся ровно к этому набору."""
    from modules import ldap_auth, groups as gr
    wanted_ids = set()
    # 1. Прямые группы из memberOf — автосоздание неизвестных
    for g in ldap_auth.fetch_groups_info(info.get('member_dns') or []):
        row = execute_query(
            "SELECT id FROM groups WHERE source = 'ad' AND ldap_dn = %s",
            (g['dn'],), fetch_one=True)
        if row:
            wanted_ids.add(row['id'])
            continue
        gid = gr.create_group(g['name'], source=gr.GROUP_SOURCE_AD,
                              ldap_dn=g['dn'], ldap_sid=g.get('sid') or '')
        if gid:
            wanted_ids.add(gid)
    # 2. Уже известные системе группы по SID из tokenGroups
    sids = set(info.get('group_sids') or ())
    if sids:
        ad_groups = execute_query(
            "SELECT id, ldap_sid FROM groups WHERE source = 'ad' AND ldap_sid <> ''",
            fetch_all=True) or []
        for row in ad_groups:
            if row['ldap_sid'] in sids:
                wanted_ids.add(row['id'])
    gr.sync_ad_memberships_by_ids(user['id'], wanted_ids)


def provision_ldap_user(info):
    """Создаёт/обновляет запись доменного пользователя.
    Доменные пользователи хранятся с доменом (user@example.com); локальный
    пользователь с тем же именем без домена может существовать параллельно.
    Старые записи без домена (по sAMAccountName) переименовываются в UPN."""
    from modules import groups as gr
    username = info.get('username') or ''
    sam = info.get('sam') or username.split('@')[0]
    if not username:
        return None
    user = execute_query(
        "SELECT * FROM users WHERE username = %s AND auth_source = 'ldap'",
        (username,), fetch_one=True)
    if not user:
        # Запись, созданная до перехода на имена с доменом
        legacy = execute_query(
            "SELECT * FROM users WHERE username = %s AND auth_source = 'ldap'",
            (sam,), fetch_one=True)
        if legacy:
            execute_query('UPDATE users SET username = %s WHERE id = %s',
                          (username, legacy['id']))
            user = legacy
    if user:
        if info.get('dn') and info['dn'] != user.get('ldap_dn'):
            execute_query('UPDATE users SET ldap_dn = %s WHERE id = %s',
                          (info['dn'], user['id']))
    else:
        import secrets
        users_group = gr.get_builtin_group(gr.BUILTIN_USERS)
        primary_gid = users_group['id'] if users_group else 1
        execute_query("""
            INSERT INTO users (username, password_hash, email, group_id,
                               auth_source, ldap_dn, nickname)
            VALUES (%s, %s, %s, %s, 'ldap', %s, %s)
        """, (username, secrets.token_hex(32), info.get('email') or None,
              primary_gid, info.get('dn') or '', info.get('display_name') or ''))
        user = execute_query(
            "SELECT * FROM users WHERE username = %s AND auth_source = 'ldap'",
            (username,), fetch_one=True)
        if user and users_group:
            gr.add_member(users_group['id'], gr.MEMBER_USER, user['id'])
    if user:
        sync_user_ad_groups(user, info)
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
                # Локальный вход: ищем только среди локальных пользователей —
                # доменный с тем же именем входит через user@domain / DOMAIN\User
                user = execute_query(
                    "SELECT * FROM users WHERE username = %s AND auth_source = 'local'",
                    (username,), fetch_one=True)
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
            session['role'] = 'admin' if gr.is_admin_user(user) else 'user'
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
                                       data.get('email'), data.get('group_id', 1),
                                       data.get('nickname') or '')
        if success:
            add_audit_log(session.get('user_id'), 'CREATE_USER', data.get('username'), message, request.remote_addr)
            return jsonify({'message': message}), 201
        return jsonify({'error': message}), 400
    
    @app.route('/api/users/<int:user_id>', methods=['PUT'])
    def update_user(user_id):
        """Редактирование пользователя (только администратор).
        У доменных пользователей логин неизменяем — это ключ синхронизации с AD."""
        from modules.auth import require_admin, add_audit_log
        from modules import groups as gr
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check

        data = request.get_json() or {}
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        is_ldap = user.get('auth_source', 'local') != 'local'

        sets, params = [], []
        username = data.get('username')
        if username is not None:
            username = str(username).strip()
            if not username:
                return jsonify({'error': 'Username cannot be empty'}), 400
            if username != user['username']:
                if is_ldap:
                    return jsonify({'error': 'Domain username cannot be changed'}), 400
                if '@' in username or '\\' in username:
                    return jsonify({'error': 'Username must not contain @ or \\'}), 400
                taken = execute_query("""
                    SELECT id FROM users
                    WHERE username = %s AND auth_source = 'local' AND id <> %s
                """, (username, user_id), fetch_one=True)
                if taken:
                    return jsonify({'error': 'Username already exists'}), 400
                sets.append('username = %s')
                params.append(username)
        nickname = data.get('nickname')
        if nickname is not None:
            sets.append('nickname = %s')
            params.append(str(nickname).strip())
        email = data.get('email')
        if email is not None:
            sets.append('email = %s')
            params.append(str(email).strip() or None)
        group_id = data.get('group_id')
        if group_id is not None:
            if not gr.group_info_by_id(group_id):
                return jsonify({'error': 'Group not found'}), 400
            sets.append('group_id = %s')
            params.append(group_id)

        if not sets:
            return jsonify({'message': 'Nothing to update'}), 200
        params.append(user_id)
        execute_query(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", tuple(params))
        add_audit_log(session.get('user_id'), 'UPDATE_USER',
                      username or user['username'], 'User updated', request.remote_addr)
        return jsonify({'message': 'User updated'}), 200

    @app.route('/api/users/<int:user_id>/groups', methods=['POST'])
    def add_user_group(user_id):
        """Добавить пользователя в группу (только администратор)"""
        from modules.auth import require_admin, add_audit_log
        from modules import groups as gr
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        body = request.get_json() or {}
        group_id = body.get('group_id')
        if not gr.group_info_by_id(group_id):
            return jsonify({'error': 'Group not found'}), 404
        ok, err = gr.add_member(group_id, gr.MEMBER_USER, user_id)
        if not ok:
            code = 404 if err in ('GroupNotFound', 'UserNotFound') else 400
            return jsonify({'error': err or 'ParamsError'}), code
        add_audit_log(session.get('user_id'), 'ADD_USER_GROUP', user['username'],
                      f'Added to group id={group_id}', request.remote_addr)
        return jsonify({'message': 'Member added'}), 200

    @app.route('/api/users/<int:user_id>/groups', methods=['DELETE'])
    def remove_user_group(user_id):
        """Исключить пользователя из группы (только администратор).
        Доменные группы у пользователей AD не удаляются — их состав
        синхронизируется из каталога при входе."""
        from modules.auth import require_admin, add_audit_log
        from modules import groups as gr
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        body = request.get_json() or {}
        group_id = body.get('group_id')
        g = gr.group_info_by_id(group_id)
        if not g:
            return jsonify({'error': 'Group not found'}), 404
        if g.get('source') == gr.GROUP_SOURCE_AD \
                and user.get('auth_source', 'local') != 'local':
            return jsonify({'error': 'AD group membership is managed by the directory'}), 400
        if g.get('builtin') == gr.BUILTIN_ADMINS and gr.is_last_admin_user(user_id):
            return jsonify({'error': 'Cannot remove the last administrator'}), 400
        gr.remove_member(group_id, gr.MEMBER_USER, user_id)
        add_audit_log(session.get('user_id'), 'REMOVE_USER_GROUP', user['username'],
                      f'Removed from group id={group_id}', request.remote_addr)
        return jsonify({'message': 'Member removed'}), 200
    
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

    # ========== ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ИЗ AD ==========
    @app.route('/api/web/ad/users', methods=['GET'])
    def web_ad_user_search():
        """Поиск пользователей в каталоге AD для добавления в систему"""
        from modules.auth import require_admin, get_all_users
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        from modules import ldap_auth
        if not ldap_auth.is_enabled():
            return jsonify({'error': 'LDAPNotConfigured'}), 400
        found = ldap_auth.search_users(request.args.get('search', ''))
        if found is None:
            return jsonify({'error': 'LDAPError'}), 500
        # Скрываем только уже добавленных из AD; локальный пользователь с тем
        # же именем не мешает — записи сосуществуют (разные auth_source и id).
        users = get_all_users()
        ldap_added = {(u.get('username') or '').lower() for u in users
                      if (u.get('auth_source') or 'local') != 'local'}
        return jsonify([u for u in found
                        if (u.get('username') or '').lower() not in ldap_added])

    @app.route('/api/web/ad/users', methods=['POST'])
    def web_ad_user_add():
        """Добавить пользователя из AD по логину (результату поиска)"""
        from modules.auth import require_admin, add_audit_log
        from modules import ldap_auth
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        body = request.get_json() or {}
        login = (body.get('username') or '').strip()
        if not login:
            return jsonify({'error': 'ParamsError'}), 400
        if not ldap_auth.is_enabled():
            return jsonify({'error': 'LDAPNotConfigured'}), 400
        info = ldap_auth.lookup_user(login)
        if not info:
            return jsonify({'error': 'UserNotFoundInAD'}), 404
        existing = execute_query(
            "SELECT id FROM users WHERE username = %s AND auth_source = 'ldap'",
            (info['username'],), fetch_one=True)
        if existing:
            return jsonify({'error': 'UserAlreadyExists'}), 400
        user = provision_ldap_user(info)
        if not user:
            return jsonify({'error': 'OperationFailed'}), 400
        add_audit_log(session.get('user_id'), 'ADD_AD_USER', user['username'],
                      f"AD user added (dn={info.get('dn', '')})", request.remote_addr)
        return jsonify({'message': 'AD user added', 'id': user['id']}), 201

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