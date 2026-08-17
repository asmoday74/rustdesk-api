import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import json
import os
import logging
from datetime import datetime
import time

# ========== НАСТРОЙКИ ПОДКЛЮЧЕНИЯ ==========
DB_DSN = os.environ.get('DB_DSN', 'postgresql://rustdesk:rustdesk@db:5432/rustdesk_monitor')

# Настройки пула соединений
DB_MIN_CONNECTIONS = 1
DB_MAX_CONNECTIONS = 20

# Глобальный пул соединений
connection_pool = None

# Логгер
db_logger = logging.getLogger('database')
db_logger.setLevel(logging.WARNING)

# ========== ИНИЦИАЛИЗАЦИЯ ПУЛА ==========
def init_db_pool():
    """Инициализирует пул соединений с PostgreSQL"""
    global connection_pool
    try:
        connection_pool = pool.SimpleConnectionPool(
            DB_MIN_CONNECTIONS,
            DB_MAX_CONNECTIONS,
            dsn=DB_DSN,
            cursor_factory=RealDictCursor,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        db_logger.info(f"Database pool initialized: min={DB_MIN_CONNECTIONS}, max={DB_MAX_CONNECTIONS}")
        
        # Проверяем подключение
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.commit()
        release_db_connection(conn)
        db_logger.info("Database connection test successful")
        return True
    except Exception as e:
        db_logger.error(f"Failed to initialize database pool: {e}")
        return False

def get_db_connection():
    """Получает соединение из пула"""
    global connection_pool
    if connection_pool is None:
        db_logger.error("Connection pool is not initialized")
        raise Exception("Database pool not initialized")
    
    try:
        conn = connection_pool.getconn()
        conn.autocommit = False
        return conn
    except Exception as e:
        db_logger.error(f"Failed to get connection from pool: {e}")
        raise

def release_db_connection(conn):
    """Возвращает соединение в пул"""
    global connection_pool
    if connection_pool is None:
        db_logger.error("Connection pool is not initialized")
        return
    try:
        connection_pool.putconn(conn)
    except Exception as e:
        db_logger.error(f"Failed to release connection: {e}")

def close_all_connections():
    """Закрывает все соединения в пуле"""
    global connection_pool
    if connection_pool is not None:
        connection_pool.closeall()
        db_logger.info("All database connections closed")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def to_dict(row):
    if row is None:
        return None
    return dict(row)

def json_serialize(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value

# ========== ОСНОВНЫЕ ФУНКЦИИ РАБОТЫ С БД ==========
def execute_query(query, params=None, fetch_one=False, fetch_all=False, retry_count=3):
    """Выполняет запрос с повторными попытками при ошибках"""
    last_error = None
    
    for attempt in range(retry_count):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            if params:
                processed_params = []
                for p in params:
                    if isinstance(p, (list, tuple, dict)):
                        processed_params.append(json.dumps(p, ensure_ascii=False))
                    else:
                        processed_params.append(p)
                cursor.execute(query, processed_params)
            else:
                cursor.execute(query)
            
            conn.commit()
            
            if fetch_one:
                result = cursor.fetchone()
                return dict(result) if result else None
            elif fetch_all:
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            else:
                return cursor.rowcount
                
        except psycopg2.OperationalError as e:
            if conn:
                conn.rollback()
            last_error = e
            error_msg = str(e).lower()
            
            if any(x in error_msg for x in ["timeout", "connection", "lock", "deadlock"]):
                if attempt < retry_count - 1:
                    wait_time = 0.5 * (attempt + 1)
                    db_logger.warning(f"Retryable error (attempt {attempt + 1}): {e}, waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
            
            db_logger.error(f"Database error: {e}")
            raise
            
        except Exception as e:
            if conn:
                conn.rollback()
            db_logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                release_db_connection(conn)
    
    if last_error:
        raise last_error

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_db():
    """Создает таблицы и индексы, если их нет"""
    db_logger.info("Initializing database...")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Создаем таблицу computers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS computers (
                id TEXT,
                uuid TEXT PRIMARY KEY,
                hostname TEXT NOT NULL,
                username TEXT DEFAULT 'Unknown',
                os TEXT DEFAULT 'Unknown',
                cpu TEXT DEFAULT 'Unknown',
                memory TEXT DEFAULT '0',
                version TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                last_update TIMESTAMPTZ DEFAULT NOW(),
                last_update_timestamp INTEGER DEFAULT EXTRACT(EPOCH FROM NOW())::INTEGER,
                last_online TIMESTAMPTZ,
                last_online_timestamp INTEGER DEFAULT 0,
                last_online_ip TEXT DEFAULT '',
                modified_at INTEGER DEFAULT 0,
                conns TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Создаем таблицу users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                email TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_login TIMESTAMPTZ
            )
        """)
        
        # Создаем таблицу audit_log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action TEXT,
                target TEXT,
                details TEXT,
                ip TEXT,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Миграция таблицы users: поля для адресной книги (совместимость с rustdesk-api)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS status INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT DEFAULT ''")

        # Таблица групп пользователей (совместимость с rustdesk-server-pro)
        # type: 1 = обычная (участник видит только себя), 2 = общая (участники видят друг друга)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                type INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Привязка устройств к владельцам для вкладки "Группа" в клиенте
        cursor.execute("ALTER TABLE computers ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE computers ADD COLUMN IF NOT EXISTS group_id INTEGER DEFAULT 0")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_computers_user_id ON computers(user_id)")

        # Таблица токенов клиентов RustDesk
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_tokens (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 0,
                device_uuid TEXT DEFAULT '',
                device_id TEXT DEFAULT '',
                token TEXT NOT NULL,
                expired_at BIGINT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Таблица адресной книги
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_books (
                row_id SERIAL PRIMARY KEY,
                id TEXT NOT NULL DEFAULT '0',
                username TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                hostname TEXT NOT NULL DEFAULT '',
                alias TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                hash TEXT NOT NULL DEFAULT '',
                user_id INTEGER NOT NULL DEFAULT 0,
                force_always_relay BOOLEAN NOT NULL DEFAULT FALSE,
                rdp_port TEXT NOT NULL DEFAULT '',
                rdp_username TEXT NOT NULL DEFAULT '',
                online BOOLEAN NOT NULL DEFAULT FALSE,
                login_name TEXT NOT NULL DEFAULT '',
                same_server BOOLEAN NOT NULL DEFAULT FALSE,
                collection_id INTEGER NOT NULL DEFAULT 0,
                device_group_name TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Миграция для существующих таблиц: поля клиента RustDesk >= 1.3
        cursor.execute("ALTER TABLE address_books ADD COLUMN IF NOT EXISTS device_group_name TEXT NOT NULL DEFAULT ''")
        cursor.execute("ALTER TABLE address_books ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT ''")

        # Коллекции (общие адресные книги)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_book_collections (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Правила доступа к коллекциям
        # rule: 1=read 2=read/write 3=full control; type: 1=personal 2=group
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS address_book_collection_rules (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 0,
                collection_id INTEGER NOT NULL DEFAULT 0,
                rule INTEGER NOT NULL DEFAULT 0,
                type INTEGER NOT NULL DEFAULT 1,
                to_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Аудит подключений клиентов RustDesk (/api/audit/*)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rustdesk_audits (
                id SERIAL PRIMARY KEY,
                audit_type TEXT NOT NULL DEFAULT 'conn',
                device_id TEXT DEFAULT '',
                uuid TEXT DEFAULT '',
                conn_id BIGINT DEFAULT 0,
                session_id BIGINT DEFAULT 0,
                action TEXT DEFAULT '',
                from_peer TEXT DEFAULT '',
                from_name TEXT DEFAULT '',
                conn_type TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                file_type INTEGER DEFAULT 0,
                path TEXT DEFAULT '',
                is_file BOOLEAN DEFAULT FALSE,
                info TEXT DEFAULT '',
                nonce TEXT DEFAULT '',
                close_time TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Теги адресной книги
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                user_id INTEGER NOT NULL DEFAULT 0,
                color BIGINT NOT NULL DEFAULT 0,
                collection_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Создаем индексы
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hostname ON computers(hostname)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_username ON computers(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_last_online ON computers(last_online_timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_computers_id ON computers(id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_tokens_token ON user_tokens(token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_tokens_user_id ON user_tokens(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_user_id ON address_books(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_id ON address_books(id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_collection_id ON address_books(collection_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_collections_user_id ON address_book_collections(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ab_rules_collection_id ON address_book_collection_rules(collection_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_collection_id ON tags(collection_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rda_device_conn ON rustdesk_audits(device_id, conn_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rda_nonce ON rustdesk_audits(nonce)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rda_created ON rustdesk_audits(created_at)")

        conn.commit()
        release_db_connection(conn)
        db_logger.info("Database tables created successfully")

        # Группа по умолчанию (id=1), на неё ссылаются существующие пользователи
        default_group = execute_query('SELECT id FROM groups WHERE id = 1', fetch_one=True)
        if not default_group:
            execute_query("""
                INSERT INTO groups (id, name, type) VALUES (1, 'Default', 1)
            """)
            # явная вставка id не двигает sequence — синхронизируем его
            execute_query("""
                SELECT setval(pg_get_serial_sequence('groups', 'id'),
                              (SELECT MAX(id) FROM groups))
            """)
            db_logger.info("Created default group id=1")

        # Проверяем наличие пользователя admin
        admin = get_user_by_username('admin')
        if not admin:
            from modules.auth import hash_password
            admin_password = hash_password('admin')
            execute_query("""
                INSERT INTO users (username, password_hash, role, group_id)
                VALUES (%s, %s, 'admin', 1)
            """, ('admin', admin_password))
            db_logger.info("Created default admin user: admin / admin")

        return True
    except Exception as e:
        db_logger.error(f"Database initialization failed: {e}")
        return False

# ========== РАБОТА С КОМПЬЮТЕРАМИ ==========
def get_computer_by_uuid(uuid):
    if not uuid:
        return None
    return execute_query(
        'SELECT * FROM computers WHERE uuid = %s',
        (uuid,),
        fetch_one=True
    )

def get_computer_by_id(computer_id):
    if not computer_id:
        return None
    return execute_query(
        'SELECT * FROM computers WHERE id = %s',
        (computer_id,),
        fetch_one=True
    )

def delete_computer_by_uuid(uuid):
    return execute_query(
        'DELETE FROM computers WHERE uuid = %s',
        (uuid,)
    ) > 0

def update_sysinfo(data, client_ip):
    uuid = data.get('uuid')
    computer_id = data.get('id')
    
    if not uuid:
        return None, 'NO_UUID'
    
    now = datetime.now()
    now_timestamp = int(now.timestamp())
    
    conns = data.get('conns')
    if isinstance(conns, (list, tuple)):
        conns = json.dumps(conns)
    
    existing = get_computer_by_uuid(uuid)
    
    if existing:
        execute_query("""
            UPDATE computers SET
                id = COALESCE(%s, id),
                hostname = %s,
                username = %s,
                os = %s,
                cpu = %s,
                memory = %s,
                version = %s,
                ip = %s,
                last_update = NOW(),
                last_update_timestamp = EXTRACT(EPOCH FROM NOW())::INTEGER,
                modified_at = COALESCE(%s, modified_at),
                conns = COALESCE(%s, conns)
            WHERE uuid = %s
        """, (
            computer_id if computer_id else existing.get('id'),
            data.get('hostname', existing.get('hostname', 'Unknown')),
            data.get('username', existing.get('username', 'Unknown')),
            data.get('os', existing.get('os', 'Unknown')),
            data.get('cpu', existing.get('cpu', 'Unknown')),
            data.get('memory', existing.get('memory', '0')),
            data.get('version', existing.get('version', '')),
            client_ip,
            data.get('modified_at', now_timestamp),
            conns,
            uuid
        ))
        result = 'UPDATED'
    else:
        execute_query("""
            INSERT INTO computers (
                id, uuid, hostname, username, os, cpu, memory, version,
                ip, last_update, last_update_timestamp, modified_at, conns, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), EXTRACT(EPOCH FROM NOW())::INTEGER, %s, %s, NOW())
        """, (
            computer_id if computer_id else '',
            uuid,
            data.get('hostname', 'Unknown'),
            data.get('username', 'Unknown'),
            data.get('os', 'Unknown'),
            data.get('cpu', 'Unknown'),
            data.get('memory', '0'),
            data.get('version', ''),
            client_ip,
            data.get('modified_at', now_timestamp),
            conns
        ))
        result = 'CREATED'
    
    return get_computer_by_uuid(uuid), result

def update_heartbeat(uuid, client_ip, conns=None, modified_at=None, computer_id=None):
    now = datetime.now()
    now_timestamp = int(now.timestamp())
    
    if isinstance(conns, (list, tuple)):
        conns = json.dumps(conns)
    
    try:
        rows_affected = execute_query("""
            UPDATE computers SET
                last_online = NOW(),
                last_online_timestamp = EXTRACT(EPOCH FROM NOW())::INTEGER,
                last_online_ip = %s,
                ip = %s,
                modified_at = COALESCE(%s, modified_at),
                conns = COALESCE(%s, conns),
                id = CASE WHEN id IS NULL OR id = '' THEN %s ELSE id END
            WHERE uuid = %s
        """, (
            client_ip,
            client_ip,
            modified_at,
            conns,
            computer_id if computer_id else None,
            uuid
        ))
        return rows_affected > 0, now_timestamp
    except Exception as e:
        db_logger.error(f"Error updating heartbeat: {e}")
        return False, None

def get_all_computers():
    return execute_query(
        'SELECT * FROM computers ORDER BY last_update_timestamp DESC',
        fetch_all=True
    )

def get_stats():
    result = execute_query('SELECT COUNT(*) as total FROM computers', fetch_one=True)
    total = result['total'] if result else 0
    
    result = execute_query("""
        SELECT COUNT(*) as online FROM computers 
        WHERE last_online_timestamp > EXTRACT(EPOCH FROM NOW())::INTEGER - 35
    """, fetch_one=True)
    online = result['online'] if result else 0
    
    return {
        'total_computers': total,
        'online_computers': online,
        'offline_computers': total - online
    }

# ========== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ==========
def get_user_by_username(username):
    return execute_query(
        'SELECT * FROM users WHERE username = %s',
        (username,),
        fetch_one=True
    )

def get_all_users():
    return execute_query(
        'SELECT id, username, role, email, group_id, created_at, last_login FROM users',
        fetch_all=True
    )

def create_user(username, password, role='user', email=None, group_id=1):
    existing = get_user_by_username(username)
    if existing:
        return False, 'Username already exists'

    from modules.auth import hash_password
    password_hash = hash_password(password)
    execute_query("""
        INSERT INTO users (username, password_hash, role, email, group_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (username, password_hash, role, email, group_id or 1))
    return True, 'User created'

def delete_user(user_id):
    admin_count = execute_query(
        'SELECT COUNT(*) as count FROM users WHERE role = %s',
        ('admin',),
        fetch_one=True
    )
    user = execute_query(
        'SELECT role FROM users WHERE id = %s',
        (user_id,),
        fetch_one=True
    )
    
    if user and user.get('role') == 'admin' and admin_count and admin_count.get('count', 0) <= 1:
        return False, 'Cannot delete the last admin user'
    
    execute_query('DELETE FROM users WHERE id = %s', (user_id,))
    return True, 'User deleted'

def update_user_last_login(user_id):
    execute_query(
        'UPDATE users SET last_login = NOW() WHERE id = %s',
        (user_id,)
    )

def add_audit_log(user_id, action, target, details, ip):
    try:
        execute_query("""
            INSERT INTO audit_log (user_id, action, target, details, ip)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, action, target, details, ip))
    except Exception as e:
        db_logger.error(f"Failed to add audit log: {e}")

def get_audit_logs(limit=100):
    return execute_query(
        'SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT %s',
        (limit,),
        fetch_all=True
    )
def get_user_by_id(user_id):
    """Получает пользователя по ID"""
    return execute_query('SELECT * FROM users WHERE id = %s', (user_id,), fetch_one=True)