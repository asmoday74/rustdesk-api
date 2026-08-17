from flask import request, jsonify
from datetime import datetime
import json
import logging

from modules.database import (
    get_computer_by_uuid, get_computer_by_id,
    update_sysinfo, update_heartbeat, get_stats, execute_query
)

sysinfo_logger = logging.getLogger('sysinfo')
heartbeat_logger = logging.getLogger('heartbeat')
error_logger = logging.getLogger('error_logger')

SERVER_VERSION = "2025.1.0"
API_VERSION = "2.0.0"

def init_public_routes(app):
    
    @app.route('/api/sysinfo', methods=['POST'])
    def register_sysinfo():
        client_ip = request.remote_addr
        raw_data = request.get_data(as_text=True)
        content_length = request.content_length
        
        if not raw_data or content_length == 0:
            sysinfo_logger.info(f"SYSINFO | IP={client_ip} | Empty request, returning version")
            return f"RustDesk Monitor v{SERVER_VERSION}", 200
        
        try:
            if request.is_json:
                data = request.get_json()
            else:
                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError as e:
                    sysinfo_logger.error(f"SYSINFO | IP={client_ip} | JSON error: {e}")
                    return "MISSING_UUID", 400
            
            if not data:
                return "MISSING_UUID", 400
            
            if 'uuid' not in data:
                sysinfo_logger.error(f"SYSINFO | IP={client_ip} | Missing UUID")
                return "MISSING_UUID", 400
            
            computer, result = update_sysinfo(data, client_ip)
            if not computer:
                return "MISSING_UUID", 400
            
            sysinfo_logger.info(f"SYSINFO | UUID={computer.get('uuid')} | Hostname={computer.get('hostname')} | Action={result}")
            return "SYSINFO_UPDATED", 200
            
        except Exception as e:
            sysinfo_logger.error(f"SYSINFO | IP={client_ip} | Error: {e}")
            return "ERROR", 500
    
    @app.route('/api/heartbeat', methods=['POST'])
    def heartbeat():
        client_ip = request.remote_addr
        raw_data = request.get_data(as_text=True)
        
        if not raw_data:
            return jsonify({}), 200
        
        try:
            if request.is_json:
                data = request.get_json()
            else:
                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError as e:
                    heartbeat_logger.error(f"HEARTBEAT | IP={client_ip} | JSON error: {e}")
                    return jsonify({}), 400
            
            if not data:
                return jsonify({}), 400
            
            uuid = data.get('uuid')
            computer_id = str(data.get('id')) if data.get('id') else None
            
            existing = None
            if uuid:
                existing = get_computer_by_uuid(uuid)
            if not existing and computer_id:
                existing = get_computer_by_id(computer_id)
            
            if not existing:
                return "", 401
            
            updated, new_timestamp = update_heartbeat(
                existing.get('uuid'), client_ip, data.get('conns'), data.get('modified_at'), computer_id
            )
            
            if updated:
                heartbeat_logger.info(f"HEARTBEAT | UUID={existing.get('uuid')} | Hostname={existing.get('hostname')}")
                return jsonify({'modified_at': new_timestamp}), 200
            return jsonify({}), 500
                
        except Exception as e:
            heartbeat_logger.error(f"HEARTBEAT | IP={client_ip} | Error: {e}")
            return jsonify({}), 500
    
    @app.route('/api/audit/<string:audit_type>', methods=['POST'])
    def client_audit(audit_type):
        """Аудит подключений от клиентов RustDesk (conn/file/alarm).
        Клиент 1.4.x ожидает пустой ответ 2xx и дедупликацию по nonce."""
        if audit_type not in ('conn', 'file', 'alarm'):
            return '', 404

        data = request.get_json(silent=True) or {}
        nonce = str(data.get('nonce') or '')
        try:
            if nonce:
                exists = execute_query(
                    'SELECT id FROM rustdesk_audits WHERE nonce = %s',
                    (nonce,), fetch_one=True
                )
                if exists:
                    return '', 200

            device_id = str(data.get('id') or '')
            uuid = str(data.get('uuid') or '')
            conn_id = int(data.get('conn_id') or 0)

            if audit_type == 'conn':
                action = str(data.get('action') or '')
                if action == 'new':
                    execute_query("""
                        INSERT INTO rustdesk_audits (
                            audit_type, device_id, uuid, conn_id, session_id,
                            action, ip, nonce
                        ) VALUES ('conn', %s, %s, %s, %s, 'new', %s, %s)
                    """, (device_id, uuid, conn_id, int(data.get('session_id') or 0),
                          str(data.get('ip') or ''), nonce))
                elif action == 'close':
                    execute_query("""
                        UPDATE rustdesk_audits SET close_time = NOW()
                        WHERE audit_type = 'conn' AND device_id = %s AND conn_id = %s
                          AND close_time IS NULL
                    """, (device_id, conn_id))
                else:
                    peer = data.get('peer') or []
                    from_peer = str(peer[0]) if isinstance(peer, list) and len(peer) > 0 else ''
                    from_name = str(peer[1]) if isinstance(peer, list) and len(peer) > 1 else ''
                    execute_query("""
                        UPDATE rustdesk_audits SET
                            from_peer = %s, from_name = %s, conn_type = %s
                        WHERE audit_type = 'conn' AND device_id = %s AND conn_id = %s
                          AND close_time IS NULL
                    """, (from_peer, from_name, str(data.get('type') or ''),
                          device_id, conn_id))
            elif audit_type == 'file':
                execute_query("""
                    INSERT INTO rustdesk_audits (
                        audit_type, device_id, uuid, conn_id, file_type,
                        path, is_file, info, nonce
                    ) VALUES ('file', %s, %s, %s, %s, %s, %s, %s, %s)
                """, (device_id, uuid, conn_id, int(data.get('type') or 0),
                      str(data.get('path') or ''), bool(data.get('is_file')),
                      str(data.get('info') or ''), nonce))
            else:
                execute_query("""
                    INSERT INTO rustdesk_audits (
                        audit_type, device_id, uuid, conn_id, file_type, info, nonce
                    ) VALUES ('alarm', %s, %s, %s, %s, %s, %s)
                """, (device_id, uuid, conn_id, int(data.get('typ') or 0),
                      str(data.get('info') or ''), nonce))
        except Exception as e:
            error_logger.error(f"AUDIT | Error: {e}")
            # Ответ с error заставляет клиента повторять отправку
            return jsonify({'error': str(e)}), 200
        return '', 200

    @app.route('/api/version', methods=['GET'])
    def get_version():
        return API_VERSION, 200
    
    @app.route('/api/sysinfo_ver', methods=['POST'])
    def sysinfo_ver():
        return SERVER_VERSION, 200
    
    @app.route('/health', methods=['GET'])
    def health_check():
        stats = get_stats()
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'computers_count': stats.get('total_computers', 0)
        })