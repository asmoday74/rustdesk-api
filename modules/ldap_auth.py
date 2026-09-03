"""LDAP-аутентификация (Active Directory) для веб-консоли и клиента.

Настройки — переменные окружения (docker-compose):
  LDAP_ENABLED      1 — включить доменную аутентификацию (по умолчанию 0)
  LDAP_SERVER       ldap://dc.domain.name:389 или ldaps://dc.domain.name:636
  LDAP_BASE_DN      база поиска, например DC=domain,DC=name
  LDAP_DOMAIN       домен для хранения доменных пользователей (user@домен);
                    по умолчанию выводится из LDAP_BASE_DN
  LDAP_BIND_DN      сервисный аккаунт для поиска пользователей (рекомендуется)
  LDAP_BIND_PASSWORD пароль сервисного аккаунта
  LDAP_USER_FILTER  фильтр поиска пользователя, {login} подставляется;
                    по умолчанию (|(sAMAccountName={login})(userPrincipalName={login}))
  LDAP_GROUP_FILTER фильтр поиска групп для интерфейса добавления, {query};
                    по умолчанию (&(objectClass=group)(name=*{query}*))
  LDAP_TIMEOUT      таймаут соединения/чтения в секундах (по умолчанию 10)

Форматы входа: user@domain.name или DOMAIN\\User. Во втором случае префикс
домена отбрасывается, поиск идёт по sAMAccountName в настроенном каталоге.
Доменные пользователи хранятся в системе как <sAMAccountName>@<LDAP_DOMAIN>,
локальным пользователям символы '@' и '\\' в логине запрещены.
"""
import logging
import os
import sys

DEFAULT_USER_FILTER = '(|(sAMAccountName={login})(userPrincipalName={login}))'
DEFAULT_GROUP_FILTER = '(&(objectClass=group)(name=*{query}*))'
GROUP_SEARCH_LIMIT = 50

_log = logging.getLogger('ldap_auth')
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - ldap_auth - %(message)s'))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


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


def _domain_from_base_dn():
    """Домен из LDAP_BASE_DN: DC=example,DC=com -> example.com"""
    parts = []
    for chunk in _env('LDAP_BASE_DN').split(','):
        chunk = chunk.strip()
        if chunk.upper().startswith('DC='):
            parts.append(chunk[3:])
    return '.'.join(parts).lower()


def ldap_domain():
    """Домен для хранения доменных пользователей (user@<домен>).
    Берётся из LDAP_DOMAIN, при отсутствии — выводится из LDAP_BASE_DN."""
    d = _env('LDAP_DOMAIN')
    return d.lower() if d else _domain_from_base_dn()


def _entry_info(entry):
    def attr(name):
        try:
            v = entry[name].value
            return str(v) if v else ''
        except Exception:
            return ''

    def attr_list(name):
        try:
            vals = entry[name].values
            return [str(v) for v in vals] if vals else []
        except Exception:
            return []
    upn = attr('userPrincipalName')
    sam = attr('sAMAccountName') or upn.split('@')[0]
    # Доменные пользователи всегда хранятся как <sAMAccountName>@<домен из настроек>
    username = ('%s@%s' % (sam, ldap_domain())) if sam else ''
    return {
        'dn': entry.entry_dn,
        'username': username,
        'sam': sam,
        'display_name': attr('displayName'),
        'email': attr('mail') or upn,
        'member_dns': attr_list('memberOf'),
        'group_sids': [],
    }


def authenticate(username, password):
    """Проверяет доменного пользователя. Возвращает словарь
    {dn, username, display_name, email, group_sids} или None.
    Причины отказа логируются (контейнер: docker logs)."""
    if not is_enabled() or not password:
        _log.warning('authenticate: LDAP disabled or empty password (user=%r)', username)
        return None
    login = parse_domain_login(username) or (username or '').strip()
    if not login:
        _log.warning('authenticate: empty login after parse (user=%r)', username)
        return None
    try:
        import ldap3
    except ImportError:
        _log.error('authenticate: ldap3 is not installed')
        return None

    base = _env('LDAP_BASE_DN')
    user_filter = _env('LDAP_USER_FILTER', DEFAULT_USER_FILTER).replace('{login}', _escape(login))
    # Для входа по user@domain дополнительно ищем по полному UPN
    # (sAMAccountName не всегда совпадает с префиксом UPN)
    raw = (username or '').strip()
    if '@' in raw:
        user_filter = f'(|{user_filter}(userPrincipalName={_escape(raw)}))'
    timeout = _timeout()
    core_attrs = ['sAMAccountName', 'displayName', 'mail', 'userPrincipalName',
                  'memberOf']
    try:
        server = _server()
        bind_dn = _env('LDAP_BIND_DN')
        if bind_dn:
            # Поиск сервисным аккаунтом, затем проверка пароля биндом от имени пользователя
            svc = ldap3.Connection(server, user=bind_dn, password=_env('LDAP_BIND_PASSWORD'),
                                   auto_bind=True, receive_timeout=timeout)
            try:
                found = svc.search(base, user_filter, search_scope=ldap3.SUBTREE,
                                   attributes=core_attrs)
                if not found or not svc.entries:
                    _log.warning('authenticate: user not found (login=%s, base=%s, result=%s)',
                                 login, base, svc.result)
                    return None
                info = _entry_info(svc.entries[0])
                info['group_sids'] = _fetch_token_groups(svc, info['dn'])
            finally:
                svc.unbind()
        else:
            # Без сервисного аккаунта: прямой бинд именем пользователя
            # (AD принимает UPN user@domain в качестве имени бинда)
            direct = ldap3.Connection(server, user=(username or '').strip(), password=password,
                                      auto_bind=True, receive_timeout=timeout)
            try:
                found = direct.search(base, user_filter, search_scope=ldap3.SUBTREE,
                                      attributes=core_attrs)
                if not found or not direct.entries:
                    _log.warning('authenticate: user not found on direct bind (login=%s, base=%s)',
                                 login, base)
                    return None
                info = _entry_info(direct.entries[0])
                info['group_sids'] = _fetch_token_groups(direct, info['dn'])
                return info
            finally:
                direct.unbind()

        # Проверка пароля пользователя
        try:
            user_conn = ldap3.Connection(server, user=info['dn'], password=password,
                                         auto_bind=True, receive_timeout=timeout)
            user_conn.unbind()
        except Exception as e:
            _log.warning('authenticate: password bind failed for %s: %s: %s',
                         info['dn'], type(e).__name__, e)
            return None
        _log.info('authenticate: ok for %s (%s)', info['username'], info['dn'])
        return info
    except Exception as e:
        _log.error('authenticate: LDAP error for login=%s: %s: %s', login, type(e).__name__, e)
        return None


def lookup_user(username):
    """Ищет пользователя в каталоге сервисным аккаунтом БЕЗ проверки пароля
    (для ручного добавления из интерфейса). Возвращает словарь
    {dn, username, display_name, email, group_sids} или None."""
    if not is_enabled():
        return None
    login = parse_domain_login(username) or (username or '').strip()
    if not login:
        return None
    try:
        import ldap3
    except ImportError:
        return None
    bind_dn = _env('LDAP_BIND_DN')
    if not bind_dn:
        _log.warning('lookup_user: LDAP_BIND_DN is required')
        return None
    base = _env('LDAP_BASE_DN')
    user_filter = _env('LDAP_USER_FILTER', DEFAULT_USER_FILTER).replace('{login}', _escape(login))
    raw = (username or '').strip()
    if '@' in raw:
        user_filter = f'(|{user_filter}(userPrincipalName={_escape(raw)}))'
    timeout = _timeout()
    core_attrs = ['sAMAccountName', 'displayName', 'mail', 'userPrincipalName',
                  'memberOf']
    try:
        server = _server()
        svc = ldap3.Connection(server, user=bind_dn, password=_env('LDAP_BIND_PASSWORD'),
                               auto_bind=True, receive_timeout=timeout)
        try:
            found = svc.search(base, user_filter, search_scope=ldap3.SUBTREE,
                               attributes=core_attrs)
            if not found or not svc.entries:
                _log.warning('lookup_user: user not found (login=%s)', login)
                return None
            info = _entry_info(svc.entries[0])
            info['group_sids'] = _fetch_token_groups(svc, info['dn'])
            return info
        finally:
            svc.unbind()
    except Exception as e:
        _log.error('lookup_user: LDAP error for login=%s: %s: %s', login, type(e).__name__, e)
        return None


DEFAULT_USER_SEARCH_FILTER = ('(&(objectClass=user)(!(objectClass=computer))'
                              '(|(sAMAccountName=*{query}*)'
                              '(userPrincipalName=*{query}*)'
                              '(displayName=*{query}*)))')
USER_SEARCH_LIMIT = 50


def search_users(query):
    """Поиск пользователей в каталоге по подстроке имени (для интерфейса
    добавления). Возвращает список {username, display_name, email, dn}
    или None при ошибке/отключённом LDAP."""
    if not is_enabled():
        return None
    q = (query or '').strip()
    if not q:
        return []
    try:
        import ldap3
    except ImportError:
        return None
    user_filter = _env('LDAP_USER_SEARCH_FILTER',
                       DEFAULT_USER_SEARCH_FILTER).replace('{query}', _escape(q))
    try:
        server = _server()
        conn = ldap3.Connection(server, user=_env('LDAP_BIND_DN') or None,
                                password=_env('LDAP_BIND_PASSWORD') or None,
                                auto_bind=True, receive_timeout=_timeout())
        try:
            conn.search(_env('LDAP_BASE_DN'), user_filter, search_scope=ldap3.SUBTREE,
                        attributes=['sAMAccountName', 'displayName', 'mail',
                                    'userPrincipalName'],
                        size_limit=USER_SEARCH_LIMIT)
            res = []
            for e in conn.entries:
                def attr(name):
                    try:
                        v = e[name].value
                        return str(v) if v else ''
                    except Exception:
                        return ''
                upn = attr('userPrincipalName')
                sam = attr('sAMAccountName') or upn.split('@')[0]
                if not sam:
                    continue
                res.append({
                    'username': '%s@%s' % (sam, ldap_domain()),
                    'display_name': attr('displayName'),
                    'email': attr('mail') or upn,
                    'dn': e.entry_dn,
                })
            return res
        finally:
            conn.unbind()
    except Exception as e:
        _log.error('search_users: LDAP error for query=%r: %s: %s', query, type(e).__name__, e)
        return None


def _fetch_token_groups(conn, dn):
    """Читает tokenGroups отдельным запросом: не все каталоги отдают этот
    вычисляемый атрибут в обычном поиске. При ошибке возвращает []."""
    try:
        import ldap3
        conn.search(dn, '(objectClass=*)', search_scope=ldap3.BASE, attributes=['tokenGroups'])
        if conn.entries:
            return _group_sids(conn.entries[0])
    except Exception as e:
        _log.warning('tokenGroups unavailable for %s: %s: %s', dn, type(e).__name__, e)
    return []


GROUP_FETCH_CHUNK = 50


def fetch_groups_info(group_dns):
    """Читает атрибуты указанных групп AD (для автосоздания в системе).
    Возвращает список {name, dn, sid}; при ошибке/пустом списке — []."""
    dns = [d for d in (group_dns or []) if d]
    if not dns or not is_enabled():
        return []
    bind_dn = _env('LDAP_BIND_DN')
    if not bind_dn:
        return []
    try:
        import ldap3
    except ImportError:
        return []
    res = []
    try:
        server = _server()
        conn = ldap3.Connection(server, user=bind_dn, password=_env('LDAP_BIND_PASSWORD'),
                                auto_bind=True, receive_timeout=_timeout())
        try:
            for i in range(0, len(dns), GROUP_FETCH_CHUNK):
                chunk = dns[i:i + GROUP_FETCH_CHUNK]
                flt = '(|' + ''.join('(distinguishedName=%s)' % _escape(d) for d in chunk) + ')'
                conn.search(_env('LDAP_BASE_DN'), flt, search_scope=ldap3.SUBTREE,
                            attributes=['name', 'sAMAccountName', 'objectSid'])
                for e in conn.entries:
                    def gattr(nm):
                        try:
                            v = e[nm].value
                            return str(v) if v else ''
                        except Exception:
                            return ''
                    name = gattr('name') or gattr('sAMAccountName')
                    if not name:
                        continue
                    sid = ''
                    try:
                        sid = _sid_to_string(e['objectSid'].raw_values[0])
                    except Exception:
                        pass
                    res.append({'name': name, 'dn': e.entry_dn, 'sid': sid})
        finally:
            conn.unbind()
    except Exception as e:
        _log.error('fetch_groups_info: LDAP error: %s: %s', type(e).__name__, e)
    return res


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
