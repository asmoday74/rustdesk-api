from flask import request, jsonify, session
from datetime import datetime

from modules.auth import (
    verify_password, add_audit_log, get_user_by_username,
    update_user_last_login, update_last_activity, get_session_timeout_seconds,
    require_admin, hash_password
)
from modules.database import execute_query, get_user_by_username as get_user

def init_auth_routes(app):
    
    @app.route('/api/login', methods=['POST'])
    def login():
        try:
            data = request.get_json()
            user = get_user(data.get('username'))
            
            if user and verify_password(user.get('password_hash'), data.get('password')):
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['login_time'] = datetime.now().isoformat()
                session['last_activity'] = datetime.now().isoformat()
                
                update_user_last_login(user['id'])
                add_audit_log(user['id'], 'LOGIN', data.get('username'), 'Successful login', request.remote_addr)
                
                return jsonify({
                    'status': 'success', 
                    'role': user['role'],
                    'session_timeout_hours': get_session_timeout_seconds() / 3600
                }), 200
            return jsonify({'error': 'Invalid credentials'}), 401
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
                                       data.get('role', 'user'), data.get('email'))
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