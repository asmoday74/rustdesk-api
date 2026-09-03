"""LDAP-аутентификация (Active Directory) для веб-консоли и клиента.

Настройки — переменные окружения (docker-compose):
  LDAP_ENABLED      1 — включить доменную аутентификацию (по умолчанию 0)
  LDAP_SERVER       ldap://dc.domain.name:389 или ldaps://dc.domain.name:636
  LDAP_BASE_DN      база поиска, например DC=domain,DC=name
  LDAP_BIND_DN      сервисный аккаунт для поиска пользователей (рекомендуется)
  LDAP_BIND_PASSWORD пароль сервисного аккаунта
  LDAP_USER_FILTER  фильтр поиска пользователя, {login} подставляется;
                    по умолчанию (|(sAMAccountName={login})(userPrincipalName={login}))
  LDAP_GROUP_FILTER фильтр поиска групп для интерфейса добавления, {query};
                    по умолчанию (&(objectClass=group)(name=*{query}*))
  LDAP_TIMEOUT      таймаут соединения/чтения в секундах (по умолчанию 10)

Форматы входа: user@domain.name или DOMAIN\\User. Во втором случае префикс
домена отбрасывается, поиск идёт по sAMAccountName в настроенном каталоге.
"""
import os

DEFAULT_USER_FILTER = '(|(sAMAccountName={login})(userPrincipalName={login}))'
DEFAULT_GROUP_FILTER = '(&(objectClass=group)(name=*{query}*))'
GROUP_SEARCH_LIMIT = 50


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def _timeout():
    try:
        return max(1, int(_env('LDAP_TIMEOUT', '10')))
    except ValueError:
        return 10


def is_enabled():
    return _env('LDAP_ENABLED', '0').lower() in ('1', 'true', 'yes') and bool(_env('LDAP_SERVER'))


def parse_domain_login(username):
    """user@domain.name или DOMAIN\\User -> логин для поиска в каталоге,
    для локального имени возвращает None"""
    u = (username or '').strip()
    if not u:
        return None
    if '\\' in u:
        return u.split('\\', 1)[1].strip() or None
    if '@' in u:
        return u.split('@', 1)[0].strip() or None
    return None


def _sid_to_string(raw):
    """Бинарный SID -> строка вида S-1-5-21-...; строка возвращается как есть"""
    if raw is None:
        return ''
    if isinstance(raw, str):
        return raw
    try:
        b = bytes(raw)
        if len(b) < 8:
            return ''
        revision = b[0]
        count = b[1]
        authority = int.from_bytes(b[2:8], 'big')
        subs = [int.from_bytes(b[8 + 4 * i:12 + 4 * i], 'little') for i in range(count)]
        return 'S-%d-%d-%s' % (revision, authority, '-'.join(str(s) for s in subs))
    except (TypeError, ValueError):
        return ''


def _server():
    import ldap3
    return ldap3.Server(_env('LDAP_SERVER'), connect_timeout=_timeout())


def _escape(value):
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(str(value))


def _group_sids(entry):
    try:
        raw_values = entry['tokenGroups'].raw_values
    except (KeyError, Exception):
        return []
    sids = [_sid_to_string(v) for v in raw_values or []]
    return [s for s in sids if s]


def _entry_info(entry):
    def attr(name):
        try:
            v = entry[name].value
            return str(v) if v else ''
        except Exception:
            return ''
    return {
        'dn': entry.entry_dn,
        'username': attr('sAMAccountName') or attr('userPrincipalName').split('@')[0],
        'display_name': attr('displayName'),
        'email': attr('mail') or attr('userPrincipalName'),
        'group_sids': _group_sids(entry),
    }


def authenticate(username, password):
    """Проверяет доменного пользователя. Возвращает словарь
    {dn, username, display_name, email, group_sids} или None."""
    if not is_enabled() or not password:
        return None
    login = parse_domain_login(username) or (username or '').strip()
    if not login:
        return None
    try:
        import ldap3
    except ImportError:
        return None

    base = _env('LDAP_BASE_DN')
    user_filter = _env('LDAP_USER_FILTER', DEFAULT_USER_FILTER).replace('{login}', _escape(login))
    timeout = _timeout()
    try:
        server = _server()
        bind_dn = _env('LDAP_BIND_DN')
        if bind_dn:
            # Поиск сервисным аккаунтом, затем проверка пароля биндом от имени пользователя
            svc = ldap3.Connection(server, user=bind_dn, password=_env('LDAP_BIND_PASSWORD'),
                                   auto_bind=True, receive_timeout=timeout)
            try:
                svc.search(base, user_filter, search_scope=ldap3.SUBTREE,
                           attributes=['sAMAccountName', 'displayName', 'mail',
                                       'userPrincipalName', 'tokenGroups'])
                if not svc.entries:
                    return None
                info = _entry_info(svc.entries[0])
            finally:
                svc.unbind()
        else:
            # Без сервисного аккаунта: прямой бинд именем пользователя
            # (AD принимает UPN user@domain в качестве имени бинда)
            direct = ldap3.Connection(server, user=(username or '').strip(), password=password,
                                      auto_bind=True, receive_timeout=timeout)
            try:
                direct.search(base, user_filter, search_scope=ldap3.SUBTREE,
                              attributes=['sAMAccountName', 'displayName', 'mail',
                                          'userPrincipalName', 'tokenGroups'])
                if not direct.entries:
                    return None
                return _entry_info(direct.entries[0])
            finally:
                direct.unbind()

        # Проверка пароля пользователя
        user_conn = ldap3.Connection(server, user=info['dn'], password=password,
                                     auto_bind=True, receive_timeout=timeout)
        user_conn.unbind()
        return info
    except Exception:
        return None


def search_groups(query):
    """Поиск групп AD по имени для интерфейса добавления.
    Возвращает список {name, dn, sid} или None при ошибке/отключённом LDAP."""
    if not is_enabled():
        return None
    try:
        import ldap3
    except ImportError:
        return None
    q = (query or '').strip()
    group_filter = _env('LDAP_GROUP_FILTER', DEFAULT_GROUP_FILTER).replace('{query}', _escape(q))
    try:
        server = _server()
        conn = ldap3.Connection(server, user=_env('LDAP_BIND_DN') or None,
                                password=_env('LDAP_BIND_PASSWORD') or None,
                                auto_bind=True, receive_timeout=_timeout())
        try:
            conn.search(_env('LDAP_BASE_DN'), group_filter, search_scope=ldap3.SUBTREE,
                        attributes=['name', 'objectSid'], size_limit=GROUP_SEARCH_LIMIT)
            res = []
            for e in conn.entries:
                sid = ''
                try:
                    sid = _sid_to_string(e['objectSid'].raw_values[0])
                except Exception:
                    pass
                res.append({
                    'name': str(e['name'].value) if e['name'] else '',
                    'dn': e.entry_dn,
                    'sid': sid,
                })
            return res
        finally:
            conn.unbind()
    except Exception:
        return None
