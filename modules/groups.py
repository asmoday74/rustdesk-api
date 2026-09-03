"""Группы пользователей: членство (пользователи и вложенные группы),
иерархические права и группы каталога (AD).

Иерархия («контейнерная» модель): группа-контейнер получает все права
вложенных в неё групп. Например, Administrators включает группу Users,
поэтому участник Administrators обладает правами обеих групп; участник
Users права группы Administrators НЕ наследует.

Права администратора (доступ к админ-панели) даёт только ПРЯМОЕ членство
пользователя во встроенной группе Administrators (ролей в системе нет);
вложенность групп на это не распространяется.
"""
from modules.database import execute_query

# Источники групп
GROUP_SOURCE_LOCAL = 'local'
GROUP_SOURCE_AD = 'ad'

# Типы членства в группе
MEMBER_USER = 'user'
MEMBER_GROUP = 'group'

# Встроенные группы (поле groups.builtin), защищены от удаления
BUILTIN_NONE = 0
BUILTIN_USERS = 1
BUILTIN_ADMINS = 2

# Типы групп (совместимость с rustdesk-server-pro, вкладка "Группа" клиента)
GROUP_TYPE_DEFAULT = 1  # участник видит только себя
GROUP_TYPE_SHARE = 2    # участники видят устройства друг друга


# ========== ГРУППЫ: CRUD ==========
def list_groups():
    return execute_query('SELECT * FROM groups ORDER BY id', fetch_all=True) or []


def group_info_by_id(group_id):
    if not group_id:
        return None
    return execute_query(
        'SELECT * FROM groups WHERE id = %s', (group_id,), fetch_one=True
    )


def get_builtin_group(builtin_flag):
    return execute_query(
        'SELECT * FROM groups WHERE builtin = %s', (builtin_flag,), fetch_one=True
    )


def group_name_exists(name, exclude_id=None):
    if exclude_id:
        row = execute_query(
            'SELECT id FROM groups WHERE name = %s AND id <> %s',
            (name, exclude_id), fetch_one=True)
    else:
        row = execute_query(
            'SELECT id FROM groups WHERE name = %s', (name,), fetch_one=True)
    return bool(row)


def create_group(name, note='', group_type=GROUP_TYPE_DEFAULT,
                 source=GROUP_SOURCE_LOCAL, ldap_dn='', ldap_sid=''):
    execute_query("""
        INSERT INTO groups (name, type, source, note, ldap_dn, ldap_sid, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """, (name, group_type, source, note or '', ldap_dn or '', ldap_sid or ''))
    row = execute_query(
        'SELECT id FROM groups WHERE name = %s ORDER BY id DESC', (name,), fetch_one=True
    )
    return row['id'] if row else None


def update_group(group_id, name=None, note=None, group_type=None):
    sets, params = [], []
    if name is not None:
        sets.append('name = %s')
        params.append(name)
    if note is not None:
        sets.append('note = %s')
        params.append(note)
    if group_type is not None:
        sets.append('type = %s')
        params.append(group_type)
    if not sets:
        return
    sets.append('updated_at = NOW()')
    params.append(group_id)
    execute_query(f"UPDATE groups SET {', '.join(sets)} WHERE id = %s", tuple(params))


def delete_group(group_id):
    """Удаляет группу; пользователей/устройства переводит в группу 1.
    Встроенные группы (Users/Administrators) и группу 1 удалять нельзя."""
    g = group_info_by_id(group_id)
    if not g or group_id == 1 or g.get('builtin', 0) != BUILTIN_NONE:
        return False
    execute_query('UPDATE users SET group_id = 1 WHERE group_id = %s', (group_id,))
    execute_query('UPDATE computers SET group_id = 1 WHERE group_id = %s', (group_id,))
    execute_query(
        'DELETE FROM address_book_collection_rules WHERE type = %s AND to_id = %s',
        (2, group_id)
    )
    execute_query('DELETE FROM group_members WHERE group_id = %s OR (member_type = %s AND member_id = %s)',
                  (group_id, MEMBER_GROUP, group_id))
    execute_query('DELETE FROM groups WHERE id = %s', (group_id,))
    return True


# ========== ЧЛЕНСТВО ==========
def add_member(group_id, member_type, member_id):
    """Возвращает (True, None) или (False, ошибка)."""
    if member_type not in (MEMBER_USER, MEMBER_GROUP):
        return False, 'ParamsError'
    if not group_info_by_id(group_id):
        return False, 'GroupNotFound'
    if member_type == MEMBER_USER:
        u = execute_query('SELECT id FROM users WHERE id = %s', (member_id,), fetch_one=True)
        if not u:
            return False, 'UserNotFound'
    else:
        if member_id == group_id:
            return False, 'CycleDetected'
        if not group_info_by_id(member_id):
            return False, 'GroupNotFound'
        # Цикл: целевая группа уже содержит добавляемую (прямо или транзитивно)
        if group_id in subgroup_closure([member_id]):
            return False, 'CycleDetected'
    execute_query("""
        INSERT INTO group_members (group_id, member_type, member_id)
        SELECT %s, %s, %s WHERE NOT EXISTS (
            SELECT 1 FROM group_members
            WHERE group_id = %s AND member_type = %s AND member_id = %s
        )
    """, (group_id, member_type, member_id, group_id, member_type, member_id))
    return True, None


def remove_member(group_id, member_type, member_id):
    execute_query("""
        DELETE FROM group_members
        WHERE group_id = %s AND member_type = %s AND member_id = %s
    """, (group_id, member_type, member_id))


def remove_user_memberships(user_id):
    execute_query(
        'DELETE FROM group_members WHERE member_type = %s AND member_id = %s',
        (MEMBER_USER, user_id))


def list_members(group_id):
    """Состав группы: {'users': [...], 'groups': [...]}"""
    rows = execute_query("""
        SELECT gm.member_type, gm.member_id FROM group_members gm
        WHERE gm.group_id = %s ORDER BY gm.member_type, gm.member_id
    """, (group_id,), fetch_all=True) or []
    user_ids = [r['member_id'] for r in rows if r['member_type'] == MEMBER_USER]
    group_ids = [r['member_id'] for r in rows if r['member_type'] == MEMBER_GROUP]
    users, groups = [], []
    if user_ids:
        ph = ','.join(['%s'] * len(user_ids))
        users = execute_query(
            f'SELECT id, username FROM users WHERE id IN ({ph}) ORDER BY username',
            tuple(user_ids), fetch_all=True) or []
    if group_ids:
        ph = ','.join(['%s'] * len(group_ids))
        groups = execute_query(
            f'SELECT id, name, source FROM groups WHERE id IN ({ph}) ORDER BY name',
            tuple(group_ids), fetch_all=True) or []
    return {'users': users, 'groups': groups}


def member_names(group_id):
    """Короткие имена членов группы (для колонки «Члены группы»)"""
    m = list_members(group_id)
    return [u['username'] for u in m['users']] + [g['name'] for g in m['groups']]


def user_direct_groups(user_id):
    """Группы, в которых пользователь состоит напрямую"""
    return execute_query("""
        SELECT g.* FROM groups g
        JOIN group_members gm ON gm.group_id = g.id
        WHERE gm.member_type = %s AND gm.member_id = %s
        ORDER BY g.id
    """, (MEMBER_USER, user_id), fetch_all=True) or []


def user_in_group(user_id, group_id):
    row = execute_query("""
        SELECT 1 FROM group_members
        WHERE group_id = %s AND member_type = %s AND member_id = %s
    """, (group_id, MEMBER_USER, user_id), fetch_one=True)
    return bool(row)


# ========== ИЕРАРХИЯ ==========
def subgroup_closure(group_ids):
    """Все группы, транзитивно вложенные в заданные (включая сами заданные).
    Направление: вниз по вложенности (member_type='group')."""
    result = set(group_ids)
    frontier = list(group_ids)
    while frontier:
        ph = ','.join(['%s'] * len(frontier))
        rows = execute_query(
            f'SELECT member_id FROM group_members WHERE group_id IN ({ph}) AND member_type = %s',
            tuple(frontier) + (MEMBER_GROUP,), fetch_all=True) or []
        frontier = [r['member_id'] for r in rows if r['member_id'] not in result]
        result.update(frontier)
    return result


def user_effective_group_ids(user_id):
    """Эффективные группы пользователя: первичная группа (users.group_id) и
    группы прямого членства плюс все группы, транзитивно в них вложенные
    (контейнер наследует права вложенных)."""
    seeds = set()
    row = execute_query('SELECT group_id FROM users WHERE id = %s', (user_id,), fetch_one=True)
    if row and row.get('group_id'):
        seeds.add(row['group_id'])
    seeds.update(g['id'] for g in user_direct_groups(user_id))
    return subgroup_closure(list(seeds))


def is_admin_user(user):
    """Права администратора: только прямое членство в группе Administrators"""
    if not user:
        return False
    row = execute_query("""
        SELECT 1 FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = %s AND gm.member_id = %s AND g.builtin = %s
    """, (MEMBER_USER, user['id'], BUILTIN_ADMINS), fetch_one=True)
    return bool(row)


def admin_member_count():
    """Количество пользователей — прямых членов группы Administrators"""
    row = execute_query("""
        SELECT COUNT(*) AS cnt FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = %s AND g.builtin = %s
    """, (MEMBER_USER, BUILTIN_ADMINS), fetch_one=True)
    return (row or {}).get('cnt', 0)


def is_last_admin_user(user_id):
    """Пользователь — последний прямой член группы Administrators"""
    if not user_id:
        return False
    row = execute_query("""
        SELECT 1 FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = %s AND gm.member_id = %s AND g.builtin = %s
    """, (MEMBER_USER, user_id, BUILTIN_ADMINS), fetch_one=True)
    return bool(row) and admin_member_count() <= 1


def add_admin_membership(user_id):
    """Добавляет пользователя во встроенную группу Administrators
    (при создании локального администратора)"""
    admins = get_builtin_group(BUILTIN_ADMINS)
    if admins:
        add_member(admins['id'], MEMBER_USER, user_id)


# ========== AD-ЧЛЕНСТВО ==========
def sync_ad_memberships(user_id, group_sids):
    """Синхронизирует членство пользователя в известных AD-группах по списку
    SID (tokenGroups из LDAP). Неизвестные системе SID игнорируются."""
    if group_sids is None:
        return
    sids = set(group_sids)
    ad_groups = execute_query(
        "SELECT id, ldap_sid FROM groups WHERE source = %s AND ldap_sid <> ''",
        (GROUP_SOURCE_AD,), fetch_all=True) or []
    wanted = {g['id'] for g in ad_groups if g['ldap_sid'] in sids}
    current = execute_query("""
        SELECT gm.group_id FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = %s AND gm.member_id = %s AND g.source = %s
    """, (MEMBER_USER, user_id, GROUP_SOURCE_AD), fetch_all=True) or []
    current_ids = {r['group_id'] for r in current}
    for gid in wanted - current_ids:
        add_member(gid, MEMBER_USER, user_id)
    for gid in current_ids - wanted:
        remove_member(gid, MEMBER_USER, user_id)


def sync_ad_memberships_by_ids(user_id, group_ids):
    """Устанавливает членство пользователя в AD-группах ровно по заданному
    набору id групп (лишние членства в группах source='ad' снимаются)."""
    wanted = set(group_ids or ())
    current = execute_query("""
        SELECT gm.group_id FROM group_members gm
        JOIN groups g ON g.id = gm.group_id
        WHERE gm.member_type = %s AND gm.member_id = %s AND g.source = %s
    """, (MEMBER_USER, user_id, GROUP_SOURCE_AD), fetch_all=True) or []
    current_ids = {r['group_id'] for r in current}
    for gid in wanted - current_ids:
        add_member(gid, MEMBER_USER, user_id)
    for gid in current_ids - wanted:
        remove_member(gid, MEMBER_USER, user_id)
