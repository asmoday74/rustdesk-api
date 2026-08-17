"""Автономные тесты sveApiRust без реального PostgreSQL.

Слой БД подменяется in-memory SQLite с трансляцией PostgreSQL-синтаксиса,
поэтому тесты проверяют логику API (адресная книга, группы, теги, коллекции,
аудит, авторизация), не требуя развёрнутой базы.

Запуск:  python tests/run_tests.py
"""
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app as app_module            # noqa: E402
import modules.database as database  # noqa: E402
import modules.ab as ab_mod          # noqa: E402
import modules.api_ab as api_ab_mod  # noqa: E402
import modules.auth as auth_mod      # noqa: E402
import modules.api_auth as api_auth_mod  # noqa: E402
import modules.clientgen as clientgen_mod  # noqa: E402
import modules.api_clientgen as api_clientgen_mod  # noqa: E402

conn = sqlite3.connect(':memory:', check_same_thread=False)
conn.row_factory = sqlite3.Row


def translate_sql(sql):
    sql = sql.replace('DEFAULT EXTRACT(EPOCH FROM NOW())::INTEGER', 'DEFAULT 0')
    sql = sql.replace('EXTRACT(EPOCH FROM NOW())::INTEGER',
                      "CAST(strftime('%s','now') AS INTEGER)")
    sql = re.sub(r'\bSERIAL PRIMARY KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql)
    sql = re.sub(r'\bTIMESTAMPTZ DEFAULT NOW\(\)', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP', sql)
    sql = re.sub(r'\bNOW\(\)', 'CURRENT_TIMESTAMP', sql)
    sql = re.sub(r'\bTIMESTAMPTZ\b', 'TIMESTAMP', sql)
    sql = sql.replace('ADD COLUMN IF NOT EXISTS', 'ADD COLUMN')
    sql = sql.replace('%s', '?')
    return sql


def _adapt(params):
    if not params:
        return ()
    out = []
    for p in params:
        if isinstance(p, bool):
            out.append(int(p))
        elif isinstance(p, (list, tuple, dict)):
            out.append(json.dumps(p, ensure_ascii=False))
        else:
            out.append(p)
    return tuple(out)


def fake_execute_query(query, params=None, fetch_one=False, fetch_all=False, retry_count=3):
    if 'pg_get_serial_sequence' in query or 'setval' in query:
        return None
    cur = conn.cursor()
    try:
        cur.execute(translate_sql(query), _adapt(params))
    except sqlite3.OperationalError as e:
        if 'duplicate column' in str(e).lower():
            return 0
        raise
    if fetch_one:
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    if fetch_all:
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    conn.commit()
    return cur.rowcount


class TransCursor:
    def __init__(self, cur):
        self.cur = cur

    def execute(self, q, params=None):
        if 'pg_get_serial_sequence' in q or 'setval' in q:
            return None
        try:
            return self.cur.execute(translate_sql(q), _adapt(params))
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower():
                return None
            raise

    def __getattr__(self, name):
        return getattr(self.cur, name)


class TransConn:
    def __init__(self, c):
        self.c = c

    def cursor(self):
        return TransCursor(self.c.cursor())

    def __getattr__(self, name):
        return getattr(self.c, name)


for _mod in (database, ab_mod, api_ab_mod, auth_mod, api_auth_mod, clientgen_mod, api_clientgen_mod):
    _mod.execute_query = fake_execute_query

database.get_db_connection = lambda: TransConn(conn)
database.release_db_connection = lambda c: None

assert database.init_db(), 'init_db failed'

flask_app = app_module.app
flask_app.config['SECRET_KEY'] = 'test'
client = flask_app.test_client()

passed = 0


def check(name, cond, extra=''):
    global passed
    if cond:
        passed += 1
        print(f'  OK  {name}')
    else:
        print(f'FAIL  {name} {extra}')
        sys.exit(1)


# ================= КЛИЕНТСКИЙ ЛОГИН =================
print('== client login ==')
r = client.post('/api/login', json={
    'username': 'admin', 'password': 'admin',
    'id': 'dev-1', 'uuid': 'uuid-1', 'autoLogin': True,
    'deviceInfo': {'name': 'PC', 'os': 'Windows', 'type': 'Windows'},
    'type': 'account',
})
check('client login 200', r.status_code == 200, r.data)
body = r.get_json()
check('access_token issued', body.get('type') == 'access_token' and body.get('access_token'))
check('user payload', body['user']['name'] == 'admin' and body['user']['is_admin'] is True)
check('user payload display_name/avatar',
      body['user'].get('display_name') == 'admin' and 'avatar' in body['user'])
token = body['access_token']
H = {'Authorization': 'Bearer ' + token}

r = client.post('/api/login', json={'username': 'admin', 'password': 'wrong', 'uuid': 'x'})
check('bad password rejected', r.status_code == 401)

print('== login-options / user info ==')
r = client.get('/api/login-options')
check('login-options []', r.status_code == 200 and r.get_json() == [])
r = client.get('/api/user/info', headers=H)
check('user/info', r.status_code == 200 and r.get_json()['name'] == 'admin')
r = client.post('/api/currentUser', headers=H)
check('currentUser', r.status_code == 200 and r.get_json()['name'] == 'admin')
r = client.get('/api/user/info')
check('user/info unauthorized', r.status_code == 401)

# ================= АДРЕСНАЯ КНИГА =================
print('== personal ab ==')
r = client.post('/api/ab/personal', headers=H)
check('personal guid', r.status_code == 200 and r.get_json()['guid'] == '1-1-0'
      and r.get_json()['rule'] == 3)
guid = r.get_json()['guid']
r = client.post('/api/ab/settings', headers=H)
check('ab settings', r.get_json() == {'max_peer_one_ab': 0})
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={guid}', headers=H)
check('peers empty', r.status_code == 200 and r.get_json()['total'] == 0)

print('== peer add/list/update ==')
peer = {
    'id': '123456789', 'username': 'john', 'hostname': 'OFFICE-PC', 'alias': 'Office',
    'platform': 'Windows', 'tags': ['work', 'office'], 'hash': 'h1', 'password': '',
    'forceAlwaysRelay': 'true', 'rdpPort': '', 'rdpUsername': '', 'online': False,
    'loginName': '', 'same_server': True, 'note': 'кабинет 205', 'device_group_name': 'grp1',
}
r = client.post(f'/api/ab/peer/add/{guid}', headers=H, json=peer)
check('peer add', r.status_code == 200, r.data)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={guid}', headers=H)
data = r.get_json()
check('peers total 1', data['total'] == 1)
p = data['data'][0]
check('peer fields', p['id'] == '123456789' and p['hostname'] == 'OFFICE-PC'
      and p['tags'] == ['work', 'office'] and p['user_id'] == 1 and 'row_id' in p)
check('forceAlwaysRelay is string', p['forceAlwaysRelay'] == 'true')
check('same_server key', p['same_server'] is True and p['sameServer'] is True)
check('note stored', p['note'] == 'кабинет 205')
check('device_group_name stored', p['device_group_name'] == 'grp1')

r = client.put(f'/api/ab/peer/update/{guid}', headers=H,
               json={'id': '123456789', 'alias': 'Office2', 'tags': ['work'],
                     'note': 'новая заметка', 'username': 'john2', 'hostname': 'NEW-HOST',
                     'platform': 'Linux'})
check('peer update', r.status_code == 200)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={guid}', headers=H)
p = r.get_json()['data'][0]
check('peer update applied', p['alias'] == 'Office2' and p['tags'] == ['work']
      and p['note'] == 'новая заметка' and p['username'] == 'john2'
      and p['hostname'] == 'NEW-HOST' and p['platform'] == 'Linux')

print('== pagination ==')
for i in range(4):
    r = client.post(f'/api/ab/peer/add/{guid}', headers=H,
                    json={'id': f'pg{i}', 'hostname': f'H{i}', 'forceAlwaysRelay': 'false'})
    assert r.status_code == 200
r = client.post(f'/api/ab/peers?current=1&pageSize=2&ab={guid}', headers=H)
d1 = r.get_json()
check('page1 size', len(d1['data']) == 2 and d1['total'] == 5)
r = client.post(f'/api/ab/peers?current=3&pageSize=2&ab={guid}', headers=H)
d3 = r.get_json()
check('page3 size', len(d3['data']) == 1 and d3['total'] == 5)
ids_all = {x['id'] for x in d1['data']} | {x['id'] for x in d3['data']}
r = client.post(f'/api/ab/peers?current=2&pageSize=2&ab={guid}', headers=H)
ids_all |= {x['id'] for x in r.get_json()['data']}
check('pages cover all peers', len(ids_all) == 5)
for i in range(4):
    client.delete(f'/api/ab/peer/{guid}', headers=H, json=[f'pg{i}'])

print('== tags ==')
r = client.post(f'/api/ab/tag/add/{guid}', headers=H, json={'name': 'work', 'color': 4288585374})
check('tag add', r.status_code == 200)
r = client.post(f'/api/ab/tag/add/{guid}', headers=H, json={'name': 'work', 'color': 1})
check('tag duplicate rejected', r.status_code == 400)
r = client.post(f'/api/ab/tag/add/{guid}', headers=H, json={'name': 'home', 'color': 4278238420})
check('tag add 2', r.status_code == 200)
r = client.post(f'/api/ab/tags/{guid}', headers=H)
tags = r.get_json()
check('tags list', len(tags) == 2 and tags[0]['name'] == 'work' and tags[0]['color'] == 4288585374)
r = client.put(f'/api/ab/tag/rename/{guid}', headers=H, json={'old': 'home', 'new': 'homework'})
check('tag rename', r.status_code == 200)
r = client.put(f'/api/ab/tag/update/{guid}', headers=H, json={'name': 'work', 'color': 123})
check('tag color update', r.status_code == 200)
r = client.post(f'/api/ab/tags/{guid}', headers=H)
tags = {t['name']: t['color'] for t in r.get_json()}
check('tag changes applied', tags == {'work': 123, 'homework': 4278238420})

print('== legacy ab ==')
r = client.get('/api/ab', headers=H)
inner = json.loads(r.get_json()['data'])
check('legacy GET /api/ab', len(inner['peers']) == 1 and inner['tags'] == ['work', 'homework']
      and json.loads(inner['tag_colors']) == {'work': 123, 'homework': 4278238420})
legacy = {
    'tags': ['t1'],
    'peers': [
        {'id': '111', 'username': 'u1', 'hostname': 'h1', 'platform': 'Linux', 'tags': ['t1'], 'hash': ''},
        {'id': '222', 'username': 'u2', 'hostname': 'h2', 'platform': 'Windows', 'tags': [], 'hash': ''},
    ],
    'tag_colors': '{"t1": 4291681337}',
}
r = client.post('/api/ab', headers=H, json={'data': json.dumps(legacy)})
check('legacy POST /api/ab', r.status_code == 200)
r = client.get('/api/ab', headers=H)
inner = json.loads(r.get_json()['data'])
check('legacy sync replaced peers', sorted(p['id'] for p in inner['peers']) == ['111', '222'])
check('legacy sync tags', inner['tags'] == ['t1'])

print('== peer delete ==')
r = client.delete(f'/api/ab/peer/{guid}', headers=H, json=['111'])
check('peer delete', r.status_code == 200)
r = client.delete(f'/api/ab/peer/{guid}', headers=H, json=['nope'])
check('peer delete not found', r.status_code == 400)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={guid}', headers=H)
check('peers after delete', r.get_json()['total'] == 1)

# ================= ГРУППА (вкладка клиента) =================
print('== group endpoints ==')
r = client.get('/api/users', headers=H)
check('GET /api/users (client mode)', r.status_code == 200 and r.get_json()['total'] == 1
      and r.get_json()['data'][0]['name'] == 'admin')
fake_execute_query("""
    INSERT INTO computers (id, uuid, hostname, username, os, user_id, group_id, last_online_timestamp)
    VALUES ('dev-owned-1', 'uuid-owned-1', 'OWNED-PC', 'john', 'Windows / 11', 1, 1, %s)
""", (int(time.time()),))
r = client.get('/api/peers', headers=H)
d = r.get_json()
check('GET /api/peers returns owned devices', r.status_code == 200 and d['total'] == 1
      and d['data'][0]['id'] == 'dev-owned-1'
      and d['data'][0]['info']['device_name'] == 'OWNED-PC'
      and d['data'][0]['user_name'] == 'admin'
      and d['data'][0]['status'] == 1)
r = client.get('/api/device-group/accessible', headers=H)
check('device-group/accessible', r.get_json() == {'total': 0, 'data': []})

# ================= ВЕБ-СЕССИЯ + КОЛЛЕКЦИИ =================
print('== web session + collections ==')
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin'})
check('web login', r.status_code == 200 and r.get_json()['status'] == 'success')
r = client.post('/api/web/ab/collections', json={'name': 'Общая ЦБК'})
check('collection create', r.status_code == 201)
r = client.get('/api/web/ab/collections')
cols = r.get_json()
check('collections list', len(cols) == 1 and cols[0]['guid'] == '1-1-1' and cols[0]['rule'] == 3)
col_guid = cols[0]['guid']
col_id = cols[0]['id']
r = client.post(f'/api/ab/peer/add/{col_guid}', json={'id': '777', 'hostname': 'SHARED-PC', 'platform': 'Windows'})
check('peer add to collection (session)', r.status_code == 200)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={col_guid}', json={})
check('collection peers', r.get_json()['total'] == 1)

# ================= ВТОРОЙ ПОЛЬЗОВАТЕЛЬ + ПРАВИЛА =================
print('== second user + sharing rules ==')
from modules.auth import create_user  # noqa: E402
ok, msg = create_user('user2', 'pass1234', 'user')
check('create user2', ok, msg)
r = client.post('/api/login', json={'username': 'user2', 'password': 'pass1234', 'uuid': 'uuid-2', 'id': 'dev-2'})
check('user2 client login', r.status_code == 200)
H2 = {'Authorization': 'Bearer ' + r.get_json()['access_token']}
r = client.post('/api/ab/shared/profiles', headers=H2)
check('user2 sees no shared abs', r.get_json()['data'] == [])
r = client.get('/api/web/ab/users')
user2_id = [u for u in r.get_json() if u['username'] == 'user2'][0]['id']
r = client.post('/api/web/ab/rules', json={'collection_id': col_id, 'type': 1, 'to_id': user2_id, 'rule': 1})
check('grant read rule', r.status_code == 200)
r = client.post('/api/ab/shared/profiles', headers=H2)
profiles = r.get_json()['data']
check('user2 sees shared ab', len(profiles) == 1 and profiles[0]['guid'] == col_guid and profiles[0]['rule'] == 1)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={col_guid}', headers=H2)
check('user2 reads shared peers', r.status_code == 200 and r.get_json()['total'] == 1)
r = client.post(f'/api/ab/peer/add/{col_guid}', headers=H2, json={'id': '888'})
check('user2 write denied (read-only)', r.status_code == 400)
r = client.post('/api/web/ab/rules', json={'collection_id': col_id, 'type': 1, 'to_id': user2_id, 'rule': 3})
check('upgrade rule to full', r.status_code == 200)
r = client.post(f'/api/ab/peer/add/{col_guid}', headers=H2, json={'id': '888', 'hostname': 'X'})
check('user2 write allowed after upgrade', r.status_code == 200)
r = client.get(f'/api/web/ab/rules/{col_id}')
rules = r.get_json()
check('rules list', len(rules) == 1 and rules[0]['target_name'] == 'user2' and rules[0]['rule'] == 3)
r = client.delete(f'/api/web/ab/rules/{rules[0]["id"]}')
check('rule delete', r.status_code == 200)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={col_guid}', headers=H2)
check('user2 access revoked', r.status_code == 400)

# ================= АУДИТ КЛИЕНТА =================
print('== client audit ==')
r = client.post('/api/audit/conn', json={'id': 'dev-9', 'uuid': 'uuid-9', 'conn_id': 42,
                                         'session_id': 1001, 'nonce': 'n-1', 'ip': '10.0.0.5', 'action': 'new'})
check('audit conn new (empty 200)', r.status_code == 200 and r.data == b'')
r = client.post('/api/audit/conn', json={'id': 'dev-9', 'uuid': 'uuid-9', 'conn_id': 42,
                                         'session_id': 1001, 'nonce': 'n-1', 'ip': '10.0.0.5', 'action': 'new'})
check('audit nonce dedup', r.status_code == 200 and r.data == b'')
r = client.post('/api/audit/conn', json={'id': 'dev-9', 'uuid': 'uuid-9', 'conn_id': 42,
                                         'session_id': 1001, 'nonce': 'n-2', 'peer': ['777', 'Иван'], 'type': 'remote'})
check('audit conn auth update', r.status_code == 200 and r.data == b'')
r = client.post('/api/audit/file', json={'id': 'dev-9', 'uuid': 'uuid-9', 'peer_id': '777', 'conn_id': 42,
                                         'type': 1, 'path': 'C:\\docs', 'is_file': False,
                                         'info': '{"ip":"10.0.0.5"}', 'nonce': 'n-3'})
check('audit file', r.status_code == 200 and r.data == b'')
r = client.post('/api/audit/alarm', json={'id': 'dev-9', 'uuid': 'uuid-9', 'typ': 2, 'info': '{}',
                                          'conn_id': 42, 'nonce': 'n-4'})
check('audit alarm', r.status_code == 200 and r.data == b'')
r = client.post('/api/audit/conn', json={'id': 'dev-9', 'conn_id': 42, 'nonce': 'n-5', 'action': 'close'})
check('audit conn close', r.status_code == 200 and r.data == b'')
rows = fake_execute_query('SELECT * FROM rustdesk_audits ORDER BY id', fetch_all=True)
check('audit rows stored', len(rows) == 3, f'rows={len(rows)}')
conn_row = [x for x in rows if x['audit_type'] == 'conn'][0]
check('audit conn closed + auth fields', conn_row['close_time'] is not None
      and conn_row['from_peer'] == '777' and conn_row['from_name'] == 'Иван'
      and conn_row['conn_type'] == 'remote')
r = client.post('/api/audit/unknown', json={})
check('audit unknown type 404', r.status_code == 404)

# ================= БЕЗОПАСНОСТЬ GUID =================
print('== guid security ==')
r = client.post('/api/ab/peers?current=1&pageSize=100&ab=1-999-0', headers=H2)
check('foreign personal ab denied', r.status_code == 400)
r = client.post('/api/ab/peers?current=1&pageSize=100&ab=abc', headers=H2)
check('bad guid denied', r.status_code == 400)

# ================= ГРУППЫ ПОЛЬЗОВАТЕЛЕЙ =================
print('== user groups ==')
r = client.post('/api/web/groups', json={'name': 'Бухгалтерия', 'type': 1})
check('group create (default)', r.status_code == 201)
grp_default = r.get_json()['id']
r = client.post('/api/web/groups', json={'name': 'ИТ-отдел', 'type': 2})
check('group create (shared)', r.status_code == 201)
grp_shared = r.get_json()['id']
r = client.get('/api/web/groups')
check('groups list (Default + 2)', r.status_code == 200 and len(r.get_json()) == 3)
r = client.post('/api/web/groups', json={'name': 'Bad', 'type': 9})
check('group invalid type rejected', r.status_code == 400)

cu = create_user
cu('alice', 'pass1234', 'user', None, grp_shared)
cu('bob', 'pass1234', 'user', None, grp_shared)
cu('carol', 'pass1234', 'user', None, grp_default)
alice_id = fake_execute_query("SELECT id FROM users WHERE username='alice'", fetch_one=True)['id']
bob_id = fake_execute_query("SELECT id FROM users WHERE username='bob'", fetch_one=True)['id']
carol_id = fake_execute_query("SELECT id FROM users WHERE username='carol'", fetch_one=True)['id']

r = client.post('/api/login', json={'username': 'alice', 'password': 'pass1234', 'uuid': 'uuid-a', 'id': 'dev-a'})
HA = {'Authorization': 'Bearer ' + r.get_json()['access_token']}
r = client.get('/api/users', headers=HA)
names = sorted(u['name'] for u in r.get_json()['data'])
check('shared group: sees members', r.get_json()['total'] == 2 and names == ['alice', 'bob'])

r = client.post('/api/login', json={'username': 'carol', 'password': 'pass1234', 'uuid': 'uuid-c', 'id': 'dev-c'})
HC = {'Authorization': 'Bearer ' + r.get_json()['access_token']}
r = client.get('/api/users', headers=HC)
check('default group: sees only self', r.get_json()['total'] == 1
      and r.get_json()['data'][0]['name'] == 'carol')

fake_execute_query("""INSERT INTO computers (id, uuid, hostname, username, os, user_id, group_id)
    VALUES ('dev-a1','uuid-a','A-PC','alice','Windows / 11', ?, ?)""", (alice_id, grp_shared))
fake_execute_query("""INSERT INTO computers (id, uuid, hostname, username, os, user_id, group_id)
    VALUES ('dev-b1','uuid-b','B-PC','bob','Linux / Ubuntu', ?, ?)""", (bob_id, grp_shared))
r = client.get('/api/peers', headers=HA)
check('shared group: sees member devices', r.get_json()['total'] == 2)
r = client.get('/api/peers', headers=HC)
check('default group: sees only own devices', r.get_json()['total'] == 0)

r = client.post('/api/web/ab/collections', json={'name': 'Корпоративная'})
check('collection for group share', r.status_code == 201)
col2 = [c for c in client.get('/api/web/ab/collections').get_json() if c['name'] == 'Корпоративная'][0]
col2_id, col2_guid = col2['id'], col2['guid']
r = client.post(f'/api/ab/peer/add/{col2_guid}', headers=H,
                json={'id': 'corp-1', 'hostname': 'CORP-PC', 'platform': 'Windows'})
check('peer added to group-shared collection', r.status_code == 200)
r = client.post('/api/web/ab/rules', json={'collection_id': col2_id, 'type': 2, 'to_id': grp_shared, 'rule': 1})
check('share collection with group', r.status_code == 200)
r = client.post('/api/ab/shared/profiles', headers=HA)
check('alice sees group-shared ab', any(p['guid'] == col2_guid for p in r.get_json()['data']))
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={col2_guid}', headers=HA)
check('alice reads group-shared peers', r.status_code == 200 and r.get_json()['total'] == 1)
r = client.post(f'/api/ab/peer/add/{col2_guid}', headers=HA, json={'id': 'x1'})
check('alice write denied (group read rule)', r.status_code == 400)
r = client.post('/api/ab/shared/profiles', headers=HC)
check('carol (outside group) does not see it',
      all(p['guid'] != col2_guid for p in r.get_json()['data']))
r = client.get(f'/api/web/ab/rules/{col2_id}')
grp_rules = [x for x in r.get_json() if x['type'] == 2]
check('group rule listed with group name', len(grp_rules) == 1
      and grp_rules[0]['target_name'] == 'ИТ-отдел')
r = client.put(f'/api/web/users/{carol_id}/group', json={'group_id': grp_shared})
check('change user group', r.status_code == 200)
r = client.post('/api/ab/shared/profiles', headers=HC)
check('carol now sees group-shared ab', any(p['guid'] == col2_guid for p in r.get_json()['data']))
r = client.put(f'/api/web/groups/{grp_default}', json={'name': 'Бухгалтерия-2', 'type': 2})
check('group update', r.status_code == 200)
r = client.delete('/api/web/groups/1')
check('cannot delete default group', r.status_code == 400)
r = client.delete(f'/api/web/groups/{grp_default}')
check('group delete', r.status_code == 200)

# ================= КОЛЛЕКЦИИ: ФИНАЛ + LOGOUT =================
print('== collection rename/delete, logout ==')
r = client.put(f'/api/web/ab/collections/{col_id}', json={'name': 'Renamed'})
check('collection rename', r.status_code == 200)
r = client.delete(f'/api/web/ab/collections/{col_id}')
check('collection delete', r.status_code == 200)
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={col_guid}', headers=H)
check('collection gone', r.status_code == 400)
r = client.post('/api/logout', headers=H2)
check('client logout', r.status_code == 200)
r = client.get('/api/user/info', headers=H2)
check('token invalidated', r.status_code == 401)

# ================= СТРАНИЦА /ab =================
print('== /ab page ==')
anon = flask_app.test_client()
r = anon.get('/ab')
check('/ab redirects unauthenticated', r.status_code in (301, 302)
      and '/login' in r.headers.get('Location', ''))
anon.post('/api/login', json={'username': 'admin', 'password': 'admin'})
r = anon.get('/ab')
check('/ab served when authenticated', r.status_code == 200 and b'data-design=' in r.data)
r = anon.get('/login')
check('/login renders', r.status_code == 200 and b'data-design=' in r.data)

# ================= СОЗДАНИЕ КЛИЕНТА (clientgen) =================
print('== clientgen ==')
r = anon.get('/clientgen')
check('/clientgen served (admin)', r.status_code == 200 and b'data-design=' in r.data)
cg_valid = {'platform': 'windows', 'version': '1.4.9', 'appname': 'TestApp',
            'direction': 'both', 'installation': 'installationY', 'settings': 'settingsY'}
r = anon.post('/api/web/clientgen/configs', json={'name': 'test-cfg', 'config_json': dict(cg_valid)})
check('clientgen create', r.status_code == 201)
cid = r.get_json()['id']
r = anon.get('/api/web/clientgen/configs')
check('clientgen list', r.status_code == 200 and any(c['id'] == cid for c in r.get_json()))
r = anon.get(f'/api/web/clientgen/configs/{cid}')
check('clientgen exename synced', r.get_json()['config_json'] and
      json.loads(r.get_json()['config_json']).get('exename') == 'test-cfg')
bad = dict(cg_valid); bad.pop('appname')
r = anon.post('/api/web/clientgen/configs', json={'name': 'test-cfg', 'config_json': bad})
check('clientgen appname required', r.status_code == 400)
r = anon.post('/api/web/clientgen/configs', json={'name': 'bad name', 'config_json': dict(cg_valid)})
check('clientgen name format', r.status_code == 400)
bad = dict(cg_valid); bad['platform'] = 'amiga'
r = anon.post('/api/web/clientgen/configs', json={'name': 'test-cfg', 'config_json': bad})
check('clientgen platform choice', r.status_code == 400)
r = anon.put(f'/api/web/clientgen/configs/{cid}', json={'name': 'test-cfg2', 'config_json': {**cg_valid, 'platform': 'linux'}})
check('clientgen update', r.status_code == 200)
r = anon.get(f'/api/web/clientgen/configs/{cid}/status')
check('clientgen status none', r.status_code == 200 and r.get_json()['build_status'] == 'none')
r = anon.post(f'/api/web/clientgen/configs/{cid}/build')
check('clientgen build blocked w/o GitHub settings', r.status_code == 400)

# ---- callback-эндпоинты генератора (GitHub Actions) ----
import io  # noqa: E402
import tempfile  # noqa: E402
clientgen_mod.DATA_DIR = tempfile.mkdtemp(prefix='clientgen-test-')
fake_execute_query(
    "UPDATE client_configs SET uuid='u-cb-1', build_token='tok-123', build_status='running' WHERE id=%s", (cid,))
r = anon.post('/updategh', json={'uuid': 'u-cb-1', 'status': 'success'})
check('updategh w/o token rejected', r.status_code == 401)
r = anon.post('/updategh', json={'uuid': 'u-cb-1', 'status': 'success'},
              headers={'Authorization': 'Bearer tok-123'})
check('updategh success', r.status_code == 200)
r = anon.get(f'/api/web/clientgen/configs/{cid}/status')
check('updategh applied', r.get_json()['build_status'] == 'success')
fake_execute_query("UPDATE client_configs SET build_status='running' WHERE id=%s", (cid,))
r = anon.post('/save_custom_client',
              data={'uuid': 'u-cb-1', 'file': (io.BytesIO(b'EXE'), 'test-cfg.exe')},
              content_type='multipart/form-data',
              headers={'Authorization': 'Bearer tok-123'})
check('save_custom_client', r.status_code == 200)
r = anon.post('/save_custom_client',
              data={'uuid': 'u-cb-1', 'file': (io.BytesIO(b'EXE'), 'x.exe')},
              content_type='multipart/form-data')
check('save_custom_client w/o token rejected', r.status_code == 401)
r = anon.get('/get_zip?filename=../../etc/passwd')
check('get_zip traversal rejected', r.status_code == 404)
r = anon.post('/cleanzip', json={})
check('cleanzip w/o uuid', r.status_code == 400)
r = anon.post('/cleanzip', json={'uuid': 'u-cb-1'})
check('cleanzip ok', r.status_code == 200)
r = anon.get('/get_png?uuid=u-cb-1&filename=icon.png')
check('get_png absent 404', r.status_code == 404)

r = anon.delete(f'/api/web/clientgen/configs/{cid}')
check('clientgen delete', r.status_code == 200)
# non-admin denied
r2 = flask_app.test_client()
r2.post('/api/login', json={'username': 'user2', 'password': 'pass1234'})
r = r2.get('/clientgen')
check('/clientgen denied for non-admin', r.status_code in (302, 403))

print(f'\nALL {passed} CHECKS PASSED')
