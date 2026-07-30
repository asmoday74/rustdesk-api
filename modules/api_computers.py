from flask import request, jsonify, session
from modules.database import get_all_computers, get_stats, delete_computer_by_uuid
from modules.auth import require_auth, require_admin, add_audit_log

def init_computers_routes(app):
    """Инициализация маршрутов для работы с компьютерами"""
    
    @app.route('/api/computers', methods=['GET'])
    def get_computers_api():
        auth_check = require_auth(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        return jsonify(get_all_computers())
    
    @app.route('/api/computers/<string:uuid>', methods=['DELETE'])
    def delete_computer(uuid):
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        success = delete_computer_by_uuid(uuid)
        if success:
            add_audit_log(session.get('user_id'), 'DELETE_COMPUTER', uuid, 'Computer deleted', request.remote_addr)
            return jsonify({'message': 'Computer deleted'}), 200
        return jsonify({'error': 'Computer not found'}), 404
    
    @app.route('/api/stats', methods=['GET'])
    def get_stats_api():
        auth_check = require_auth(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        stats = get_stats()
        stats['api_version'] = '2.0.0'
        return jsonify(stats)
    
    @app.route('/api/audit', methods=['GET'])
    def get_audit_logs():
        from modules.database import execute_query
        auth_check = require_admin(lambda: None)()
        if isinstance(auth_check, tuple):
            return auth_check
        
        limit = request.args.get('limit', 100, type=int)
        logs = execute_query('SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?', (limit,), fetch_all=True)
        return jsonify(logs if logs else [])