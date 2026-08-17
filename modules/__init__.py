# Модули базы данных
from modules.database import (
    get_db_connection,
    release_db_connection,
    execute_query,
    init_db,
    init_db_pool,
    close_all_connections,
    get_computer_by_uuid,
    get_computer_by_id,
    delete_computer_by_uuid,
    update_sysinfo,
    update_heartbeat,
    get_all_computers,
    get_stats,
    get_user_by_username,
    get_user_by_id,  # Добавлено
    get_all_users,
    create_user,
    delete_user,
    update_user_last_login,
    add_audit_log,
    get_audit_logs
)

# Модули аутентификации
from modules.auth import (
    hash_password,
    verify_password,
    require_auth,
    require_admin,
    update_last_activity,
    is_session_expired,
    get_session_timeout_seconds
)

# Модули API
from modules.api_auth import init_auth_routes
from modules.api_computers import init_computers_routes
from modules.api_public import init_public_routes
from modules.api_ab import init_ab_routes
from modules.api_clientgen import init_clientgen_routes