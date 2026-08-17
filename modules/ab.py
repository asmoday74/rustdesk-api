import json
import os
import secrets
import time

from modules.database import execute_query

# ========== НАСТРОЙКИ ==========
# Время жизни токена клиента RustDesk (секунды), по умолчанию 7 дней
TOKEN_EXPIRE_SECONDS = int(os.environ.get('TOKEN_EXPIRE_SECONDS', 604800))
# Режим личных адресных книг (1 - включен, 0 - выключен), как rustdesk.personal в оригинале
AB_PERSONAL = int(os.environ.get('AB_PERSONAL', 1))

# Правила доступа к общим адресным книгам
RULE_READ = 1
RULE_READ_WRITE = 2
RULE_FULL_CONTROL = 3

# Типы правил доступа
RULE_TYPE_PERSONAL = 1
RULE_TYPE_GROUP = 2

STATUS_ENABLE = 1

# Типы групп (совместимость с rustdesk-server-pro)
GROUP_TYPE_DEFAULT = 1  # участник видит только себя
GROUP_TYPE_SHARE = 2    # участники видят устройства друг друга


# ========== ТОКЕНЫ КЛИЕНТОВ ==========
def token_expire_timestamp():
    return int(time.time()) + TOKEN_EXPIRE_SECONDS


def create_user_token(user, device_id='', device_uuid=''):
    token = secrets.token_hex(16)
    execute_query('DELETE FROM user_tokens WHERE expired_at < %s', (int(time.time()),))
    execute_query("""
        INSERT INTO user_tokens (user_id, device_uuid, device_id, token, expired_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (user['id'], device_uuid or '', device_id or '', token, token_expire_timestamp()))
    return token


def get_token_info(token):
    if not token:
        return None
    return execute_query(
        'SELECT * FROM user_tokens WHERE token = %s',
        (token,),
        fetch_one=True
    )


def get_user_by_access_token(token):
    """Возвращает (user, token_info) или (None, None)"""
    ut = get_token_info(token)
    if not ut:
        return None, None
    if ut.get('expired_at', 0) < int(time.time()):
        return None, None
    user = execute_query('SELECT * FROM users WHERE id = %s', (ut['user_id'],), fetch_one=True)
    if not user:
        return None, None
    return user, ut


def auto_refresh_token(ut):
    """Продлевает токен, если осталось меньше 1/3 срока жизни"""
    if not ut:
        return
    remaining = ut.get('expired_at', 0) - int(time.time())
    if remaining < TOKEN_EXPIRE_SECONDS / 3:
        execute_query(
            'UPDATE user_tokens SET expired_at = %s, updated_at = NOW() WHERE id = %s',
            (token_expire_timestamp(), ut['id'])
        )


def delete_user_token(user_id, token):
    execute_query(
        'DELETE FROM user_tokens WHERE user_id = %s AND token = %s',
        (user_id, token)
    )


def is_user_enabled(user):
    return user.get('status', STATUS_ENABLE) == STATUS_ENABLE


# ========== GUID ==========
def compose_guid(gid, uid, cid):
    return f"{gid}-{uid}-{cid}"


def parse_guid(guid):
    """Возвращает (gid, uid, cid) или (0, 0, 0) при ошибке"""
    try:
        parts = str(guid).split('-')
        if len(parts) < 2:
            return 0, 0, 0
        gid = int(parts[0])
        uid = int(parts[1])
        cid = int(parts[2]) if len(parts) >= 3 else 0
        return gid, uid, cid
    except (ValueError, TypeError):
        return 0, 0, 0


def check_guid(cur_user, guid):
    """Проверяет guid. Возвращает (gid, uid, cid, error)"""
    gid, uid, cid = parse_guid(guid)
    if gid == 0 or uid == 0:
        return 0, 0, 0, 'ParamsError'

    if cur_user['id'] == uid:
        u = cur_user
    else:
        u = execute_query('SELECT * FROM users WHERE id = %s', (uid,), fetch_one=True)

    if not u:
        return 0, 0, 0, 'ParamsError'
    if u.get('group_id', 1) != gid:
        return 0, 0, 0, 'ParamsError'
    if cid == 0 and cur_user['id'] != uid:
        return 0, 0, 0, 'ParamsError'
    if cid > 0:
        c = collection_info_by_id(cid)
        if not c:
            return 0, 0, 0, 'ParamsError'
        if c['user_id'] != uid:
            return 0, 0, 0, 'ParamsError'
    return gid, uid, cid, None


def personal_guid(user):
    return compose_guid(user.get('group_id', 1), user['id'], 0)


# ========== ПРАВА ДОСТУПА ==========
def user_max_rule(user, uid, cid):
    if user['id'] == uid:
        return RULE_FULL_CONTROL
    max_rule = 0
    personal_rule = execute_query("""
        SELECT * FROM address_book_collection_rules
        WHERE type = %s AND collection_id = %s AND to_id = %s
        LIMIT 1
    """, (RULE_TYPE_PERSONAL, cid, user['id']), fetch_one=True)
    if personal_rule:
        max_rule = personal_rule['rule']
        if max_rule == RULE_FULL_CONTROL:
            return max_rule

    group_rule = execute_query("""
        SELECT * FROM address_book_collection_rules
        WHERE type = %s AND collection_id = %s AND to_id = %s
        LIMIT 1
    """, (RULE_TYPE_GROUP, cid, user.get('group_id', 1)), fetch_one=True)
    if group_rule and group_rule['rule'] > max_rule:
        max_rule = group_rule['rule']
    return max_rule


def check_read_privilege(user, uid, cid):
    return user_max_rule(user, uid, cid) >= RULE_READ


def check_write_privilege(user, uid, cid):
    return user_max_rule(user, uid, cid) >= RULE_READ_WRITE


def check_full_control_privilege(user, uid, cid):
    return user_max_rule(user, uid, cid) >= RULE_FULL_CONTROL


def collection_read_rules(user):
    """Правила, по которым пользователь может читать чужие коллекции"""
    personal_rules = execute_query("""
        SELECT * FROM address_book_collection_rules
        WHERE type = %s AND to_id = %s AND rule > 0
    """, (RULE_TYPE_PERSONAL, user['id']), fetch_all=True) or []
    group_rules = execute_query("""
        SELECT * FROM address_book_collection_rules
        WHERE type = %s AND to_id = %s AND rule > 0
    """, (RULE_TYPE_GROUP, user.get('group_id', 1)), fetch_all=True) or []
    return personal_rules + group_rules


# ========== СЕРИАЛИЗАЦИЯ ==========
def parse_tags(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        res = json.loads(value)
        return res if isinstance(res, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def ab_to_payload(row):
    """Строка БД -> JSON для клиента RustDesk.
    forceAlwaysRelay - строка "true"/"false" (Peer.fromJson клиента 1.4.x
    сравнивает со строкой), same_server дублируется в двух написаниях."""
    return {
        'row_id': row['row_id'],
        'id': row['id'],
        'username': row['username'],
        'password': row['password'],
        'hostname': row['hostname'],
        'alias': row['alias'],
        'platform': row['platform'],
        'tags': parse_tags(row['tags']),
        'hash': row['hash'],
        'user_id': row['user_id'],
        'forceAlwaysRelay': 'true' if row['force_always_relay'] else 'false',
        'rdpPort': row['rdp_port'],
        'rdpUsername': row['rdp_username'],
        'online': bool(row['online']),
        'loginName': row['login_name'],
        'sameServer': bool(row['same_server']),
        'same_server': bool(row['same_server']),
        'device_group_name': row.get('device_group_name') or '',
        'note': row.get('note') or '',
        'collection_id': row['collection_id'],
    }


def tag_to_payload(row):
    return {
        'id': row['id'],
        'name': row['name'],
        'user_id': row['user_id'],
        'color': row['color'],
        'collection_id': row['collection_id'],
    }


def user_payload(user):
    return {
        'name': user['username'],
        'display_name': user.get('nickname') or user['username'],
        'avatar': '',
        'email': user.get('email') or '',
        'note': '',
        'is_admin': user.get('role') == 'admin',
        'status': user.get('status', STATUS_ENABLE),
        'info': {},
    }


def platform_from_os(os_name):
    if not os_name:
        return ''
    o = os_name.lower()
    if 'android' in o:
        return 'Android'
    if 'windows' in o:
        return 'Windows'
    if 'linux' in o:
        return 'Linux'
    if 'mac' in o:
        return 'Mac OS'
    return ''


def find_peer_by_id(peer_id):
    """Устройство из таблицы computers по ID (для автозаполнения полей)"""
    if not peer_id:
        return None
    return execute_query(
        'SELECT * FROM computers WHERE id = %s',
        (str(peer_id),),
        fetch_one=True
    )


def fill_ab_from_peer(ab):
    """Дополняет platform/username/hostname данными из computers, если они пустые"""
    if not ab.get('platform') or not ab.get('username') or not ab.get('hostname'):
        peer = find_peer_by_id(ab.get('id'))
        if peer:
            if not ab.get('platform'):
                ab['platform'] = platform_from_os(peer.get('os'))
            if not ab.get('username'):
                ab['username'] = peer.get('username') or ''
            if not ab.get('hostname'):
                ab['hostname'] = peer.get('hostname') or ''
    return ab


# ========== АДРЕСНАЯ КНИГА ==========
def list_ab(user_id, collection_id, page=1, page_size=1000):
    """Страницы с 1. Клиент RustDesk запрашивает current/pageSize"""
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1000
    offset = (page - 1) * page_size
    rows = execute_query("""
        SELECT * FROM address_books
        WHERE user_id = %s AND collection_id = %s
        ORDER BY row_id
        LIMIT %s OFFSET %s
    """, (user_id, collection_id, page_size, offset), fetch_all=True) or []
    return rows


def count_ab(user_id, collection_id):
    result = execute_query("""
        SELECT COUNT(*) AS cnt FROM address_books
        WHERE user_id = %s AND collection_id = %s
    """, (user_id, collection_id), fetch_one=True)
    return result['cnt'] if result else 0


def ab_info(user_id, peer_id, collection_id):
    return execute_query("""
        SELECT * FROM address_books
        WHERE user_id = %s AND id = %s AND collection_id = %s
    """, (user_id, str(peer_id), collection_id), fetch_one=True)


def _to_bool(value):
    # Клиент присылает forceAlwaysRelay как строку "true"/"false"
    if isinstance(value, str):
        return value.lower() == 'true'
    return bool(value)


def _ab_insert_values(ab, user_id, collection_id):
    return (
        str(ab.get('id') or '0'),
        ab.get('username') or '',
        ab.get('password') or '',
        ab.get('hostname') or '',
        ab.get('alias') or '',
        ab.get('platform') or '',
        json.dumps(ab.get('tags') or [], ensure_ascii=False),
        ab.get('hash') or '',
        user_id,
        _to_bool(ab.get('forceAlwaysRelay', ab.get('force_always_relay'))),
        ab.get('rdpPort') or ab.get('rdp_port') or '',
        ab.get('rdpUsername') or ab.get('rdp_username') or '',
        _to_bool(ab.get('online')),
        ab.get('loginName') or ab.get('login_name') or '',
        _to_bool(ab.get('same_server', ab.get('sameServer'))),
        collection_id,
        ab.get('device_group_name') or '',
        ab.get('note') or '',
    )


def add_ab(ab, user_id, collection_id):
    ab = fill_ab_from_peer(ab)
    execute_query("""
        INSERT INTO address_books (
            id, username, password, hostname, alias, platform, tags, hash,
            user_id, force_always_relay, rdp_port, rdp_username, online,
            login_name, same_server, collection_id, device_group_name, note, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    """, _ab_insert_values(ab, user_id, collection_id))


def update_ab_full(row_id, ab, user_id, collection_id):
    ab = fill_ab_from_peer(ab)
    execute_query("""
        UPDATE address_books SET
            id = %s, username = %s, password = %s, hostname = %s, alias = %s,
            platform = %s, tags = %s, hash = %s, user_id = %s,
            force_always_relay = %s, rdp_port = %s, rdp_username = %s, online = %s,
            login_name = %s, same_server = %s, collection_id = %s,
            device_group_name = %s, note = %s, updated_at = NOW()
        WHERE row_id = %s
    """, _ab_insert_values(ab, user_id, collection_id) + (row_id,))


def update_ab_by_map(row_id, data):
    """Обновление только разрешенных полей"""
    allowed = {
        'password': 'password', 'hash': 'hash', 'alias': 'alias', 'tags': 'tags',
        'note': 'note', 'username': 'username', 'hostname': 'hostname',
        'platform': 'platform', 'device_group_name': 'device_group_name',
    }
    sets, params = [], []
    for key, column in allowed.items():
        if key in data:
            value = data[key]
            if key == 'tags':
                value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        return
    sets.append("updated_at = NOW()")
    params.append(row_id)
    execute_query(f"UPDATE address_books SET {', '.join(sets)} WHERE row_id = %s", tuple(params))


def delete_ab(row_id):
    execute_query('DELETE FROM address_books WHERE row_id = %s', (row_id,))


def sync_address_book(peers, user_id, collection_id=0):
    """Полная синхронизация: добавить новые, обновить существующие, удалить отсутствующие"""
    db_rows = list_ab(user_id, collection_id, page=1, page_size=100000)
    incoming = {}
    for p in peers or []:
        pid = str(p.get('id') or '')
        incoming[pid] = p
    db_by_id = {r['id']: r for r in db_rows}

    for pid, p in incoming.items():
        if pid in db_by_id:
            update_ab_full(db_by_id[pid]['row_id'], p, user_id, collection_id)
        else:
            add_ab(p, user_id, collection_id)

    for pid, row in db_by_id.items():
        if pid not in incoming:
            delete_ab(row['row_id'])


# ========== ТЕГИ ==========
def list_tags(user_id, collection_id):
    return execute_query("""
        SELECT * FROM tags
        WHERE user_id = %s AND collection_id = %s
        ORDER BY id
    """, (user_id, collection_id), fetch_all=True) or []


def tag_info(user_id, name, collection_id):
    return execute_query("""
        SELECT * FROM tags
        WHERE user_id = %s AND name = %s AND collection_id = %s
    """, (user_id, name, collection_id), fetch_one=True)


def add_tag(user_id, name, collection_id, color=0):
    execute_query("""
        INSERT INTO tags (name, user_id, color, collection_id, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, (name, user_id, int(color or 0), collection_id))


def update_tag(tag_id, name=None, color=None):
    if name is not None and color is not None:
        execute_query(
            'UPDATE tags SET name = %s, color = %s, updated_at = NOW() WHERE id = %s',
            (name, int(color), tag_id)
        )
    elif name is not None:
        execute_query(
            'UPDATE tags SET name = %s, updated_at = NOW() WHERE id = %s',
            (name, tag_id)
        )
    elif color is not None:
        execute_query(
            'UPDATE tags SET color = %s, updated_at = NOW() WHERE id = %s',
            (int(color), tag_id)
        )


def delete_tag(tag_id):
    execute_query('DELETE FROM tags WHERE id = %s', (tag_id,))


def sync_tags(user_id, tag_colors, collection_id=0):
    """Синхронизация тегов из tag_colors (legacy POST /api/ab)"""
    if not isinstance(tag_colors, dict):
        return
    existing = {t['name']: t for t in list_tags(user_id, collection_id)}
    for name, color in tag_colors.items():
        if name in existing:
            update_tag(existing[name]['id'], color=color)
        else:
            add_tag(user_id, name, collection_id, color)
    for name, t in existing.items():
        if name not in tag_colors:
            delete_tag(t['id'])


# ========== КОЛЛЕКЦИИ ==========
def collection_info_by_id(collection_id):
    return execute_query(
        'SELECT * FROM address_book_collections WHERE id = %s',
        (collection_id,),
        fetch_one=True
    )


def list_collections_by_user(user_id):
    return execute_query("""
        SELECT * FROM address_book_collections
        WHERE user_id = %s ORDER BY id
    """, (user_id,), fetch_all=True) or []


def list_collections_by_ids(ids):
    if not ids:
        return []
    placeholders = ','.join(['%s'] * len(ids))
    return execute_query(
        f'SELECT * FROM address_book_collections WHERE id IN ({placeholders}) ORDER BY id',
        tuple(ids),
        fetch_all=True
    ) or []


def create_collection(user_id, name):
    execute_query("""
        INSERT INTO address_book_collections (user_id, name, updated_at)
        VALUES (%s, %s, NOW())
    """, (user_id, name))


def rename_collection(collection_id, name):
    execute_query(
        'UPDATE address_book_collections SET name = %s, updated_at = NOW() WHERE id = %s',
        (name, collection_id)
    )


def delete_collection(collection_id):
    execute_query('DELETE FROM address_book_collection_rules WHERE collection_id = %s', (collection_id,))
    execute_query('DELETE FROM address_books WHERE collection_id = %s', (collection_id,))
    execute_query('DELETE FROM tags WHERE collection_id = %s', (collection_id,))
    execute_query('DELETE FROM address_book_collections WHERE id = %s', (collection_id,))


# ========== ПРАВИЛА ДОСТУПА ==========
def list_rules_by_collection(collection_id):
    return execute_query("""
        SELECT * FROM address_book_collection_rules
        WHERE collection_id = %s ORDER BY id
    """, (collection_id,), fetch_all=True) or []


def rule_info_by_id(rule_id):
    return execute_query(
        'SELECT * FROM address_book_collection_rules WHERE id = %s',
        (rule_id,),
        fetch_one=True
    )


def add_rule(user_id, collection_id, rule, rule_type, to_id):
    execute_query("""
        INSERT INTO address_book_collection_rules (user_id, collection_id, rule, type, to_id, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (user_id, collection_id, rule, rule_type, to_id))


def update_rule(rule_id, rule):
    execute_query(
        'UPDATE address_book_collection_rules SET rule = %s, updated_at = NOW() WHERE id = %s',
        (rule, rule_id)
    )


def delete_rule(rule_id):
    execute_query('DELETE FROM address_book_collection_rules WHERE id = %s', (rule_id,))


# ========== ПОЛЬЗОВАТЕЛИ ГРУППЫ / ПИРЫ ==========
def list_users_by_group(group_id):
    return execute_query("""
        SELECT * FROM users WHERE group_id = %s ORDER BY id
    """, (group_id,), fetch_all=True) or []


# ========== ГРУППЫ ==========
def list_groups():
    return execute_query('SELECT * FROM groups ORDER BY id', fetch_all=True) or []


def group_info_by_id(group_id):
    if not group_id:
        return None
    return execute_query(
        'SELECT * FROM groups WHERE id = %s', (group_id,), fetch_one=True
    )


def create_group(name, group_type=GROUP_TYPE_DEFAULT):
    execute_query("""
        INSERT INTO groups (name, type, updated_at) VALUES (%s, %s, NOW())
    """, (name, group_type))
    row = execute_query(
        'SELECT id FROM groups WHERE name = %s ORDER BY id DESC', (name,), fetch_one=True
    )
    return row['id'] if row else None


def update_group(group_id, name=None, group_type=None):
    sets, params = [], []
    if name is not None:
        sets.append('name = %s')
        params.append(name)
    if group_type is not None:
        sets.append('type = %s')
        params.append(group_type)
    if not sets:
        return
    sets.append('updated_at = NOW()')
    params.append(group_id)
    execute_query(f"UPDATE groups SET {', '.join(sets)} WHERE id = %s", tuple(params))


def delete_group(group_id):
    """Удаляет группу; её пользователи и устройства переходят в группу 1"""
    if group_id == 1:
        return False
    execute_query('UPDATE users SET group_id = 1 WHERE group_id = %s', (group_id,))
    execute_query('UPDATE computers SET group_id = 1 WHERE group_id = %s', (group_id,))
    execute_query(
        'DELETE FROM address_book_collection_rules WHERE type = %s AND to_id = %s',
        (RULE_TYPE_GROUP, group_id)
    )
    execute_query('DELETE FROM groups WHERE id = %s', (group_id,))
    return True


def can_see_group_members(user):
    """Админ или участник общей группы видит всех участников группы"""
    if user.get('role') == 'admin':
        return True
    group = group_info_by_id(user.get('group_id', 1))
    return bool(group and group.get('type') == GROUP_TYPE_SHARE)


def set_user_group(user_id, group_id):
    execute_query('UPDATE users SET group_id = %s WHERE id = %s', (group_id, user_id))


# ========== ПРИВЯЗКА УСТРОЙСТВ К ПОЛЬЗОВАТЕЛЯМ ==========
def bind_device_user(device_uuid, user):
    """При входе клиента привязывает устройство (по uuid) к пользователю"""
    if not device_uuid:
        return
    execute_query("""
        UPDATE computers SET user_id = %s, group_id = %s
        WHERE uuid = %s
    """, (user['id'], user.get('group_id', 1), device_uuid))


def unbind_device_user(device_uuid, user_id):
    if not device_uuid:
        return
    execute_query("""
        UPDATE computers SET user_id = 0
        WHERE uuid = %s AND user_id = %s
    """, (device_uuid, user_id))


def list_computers_by_user_ids(user_ids, limit=1000):
    """Устройства, принадлежащие списку пользователей (для вкладки Группа)"""
    if not user_ids:
        return []
    placeholders = ','.join(['%s'] * len(user_ids))
    return execute_query(f"""
        SELECT * FROM computers
        WHERE user_id IN ({placeholders})
        ORDER BY last_update_timestamp DESC NULLS LAST
        LIMIT %s
    """, tuple(user_ids) + (limit,), fetch_all=True) or []
