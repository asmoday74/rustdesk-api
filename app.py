from flask import Flask, send_from_directory, session, redirect, url_for, jsonify, request
from datetime import datetime
import os
import logging
from logging.handlers import RotatingFileHandler
import sys

# Импорты из модулей
from modules import (
    init_db, init_db_pool, close_all_connections,
    get_user_by_username, create_user, add_audit_log,
    init_auth_routes, init_computers_routes, init_public_routes
)

app = Flask(__name__, static_folder='static', static_url_path='/static')

# ========== НАСТРОЙКИ ==========
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
log_format = '%(asctime)s - %(levelname)s - %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'

os.makedirs('/data', exist_ok=True)

sysinfo_logger = logging.getLogger('sysinfo')
sysinfo_logger.setLevel(logging.INFO)
sysinfo_handler = RotatingFileHandler('/data/sysinfo.log', maxBytes=10485760, backupCount=10)
sysinfo_handler.setFormatter(logging.Formatter(log_format, date_format))
sysinfo_logger.addHandler(sysinfo_handler)

heartbeat_logger = logging.getLogger('heartbeat')
heartbeat_logger.setLevel(logging.INFO)
heartbeat_handler = RotatingFileHandler('/data/heartbeat.log', maxBytes=10485760, backupCount=10)
heartbeat_handler.setFormatter(logging.Formatter(log_format, date_format))
heartbeat_logger.addHandler(heartbeat_handler)

error_logger = logging.getLogger('error_logger')
error_logger.setLevel(logging.ERROR)
error_handler = logging.StreamHandler()
error_handler.setFormatter(logging.Formatter(log_format, date_format))
error_logger.addHandler(error_handler)

try:
    error_file_handler = RotatingFileHandler('/data/errors.log', maxBytes=10485760, backupCount=10)
    error_file_handler.setFormatter(logging.Formatter(log_format, date_format))
    error_logger.addHandler(error_file_handler)
except:
    pass

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
app.logger.disabled = True

# ========== ВЕБ-МАРШРУТЫ ==========
@app.route('/login')
def login_page():
    return send_from_directory('static', 'login.html')

@app.route('/')
def index():
    from modules.auth import require_auth
    auth_check = require_auth(lambda: None)()
    if isinstance(auth_check, tuple):
        return auth_check
    return send_from_directory('static', 'index.html')

@app.route('/admin')
def admin_page():
    from modules.auth import require_admin
    auth_check = require_admin(lambda: None)()
    if isinstance(auth_check, tuple):
        return auth_check
    return send_from_directory('static', 'admin.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

@app.route('/logout')
def web_logout():
    user_id = session.get('user_id')
    username = session.get('username', 'Unknown')
    if user_id:
        add_audit_log(user_id, 'LOGOUT', username, 'Web logout', request.remote_addr)
    session.clear()
    return redirect(url_for('login_page'))

# ========== ЭНДПОИНТЫ ДЛЯ ДИАГНОСТИКИ ==========
@app.route('/api/db/health', methods=['GET'])
def db_health():
    """Проверка состояния базы данных"""
    from modules.database import get_db_connection, release_db_connection
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1')
        conn.commit()
        release_db_connection(conn)
        return jsonify({'status': 'ok', 'database': 'postgresql'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/db/repair', methods=['POST'])
def db_repair():
    """Принудительное восстановление базы данных"""
    from modules.database import get_db_connection, release_db_connection
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1')
        conn.commit()
        release_db_connection(conn)
        return jsonify({'status': 'repaired'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ========== ИНИЦИАЛИЗАЦИЯ API ==========
init_auth_routes(app)
init_computers_routes(app)
init_public_routes(app)

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    os.makedirs('/data', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # Инициализация пула соединений с PostgreSQL
    if not init_db_pool():
        error_logger.error("Failed to initialize database pool. Exiting.")
        sys.exit(1)
    
    # Инициализация таблиц
    if not init_db():
        error_logger.error("Failed to initialize database. Exiting.")
        sys.exit(1)
    
    print("=" * 60)
    print("🚀 RustDesk Monitor Server v6.0 (PostgreSQL)")
    print("=" * 60)
    print(f"📁 Database: PostgreSQL (connection pool)")
    print(f"🌐 Web UI: http://0.0.0.0:21114")
    print(f"🔐 Login: http://0.0.0.0:21114/login (admin/admin)")
    print("=" * 60)
    print("📁 Database pool:")
    print(f"   - Min connections: 1")
    print(f"   - Max connections: 20")
    print("=" * 60)
    
    try:
        app.run(host='0.0.0.0', port=21114, debug=False, threaded=True)
    finally:
        close_all_connections()