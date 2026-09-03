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

print('== peers online status ==')
fake_execute_query("""
    INSERT INTO computers (id, uuid, hostname, username, os, last_online_timestamp)
    VALUES ('111', 'uuid-peer-111', 'PEER-111', 'u1', 'Windows / 11', %s)
""", (int(time.time()),))
r = client.post(f'/api/ab/peers?current=1&pageSize=100&ab={guid}', headers=H)
peers_by_id = {p['id']: p for p in r.get_json()['data']}
check('peer with recent computer is online', peers_by_id['111']['online'] is True)
check('peer without computer stays offline', peers_by_id['222']['online'] is False)
r = client.get('/api/ab', headers=H)
legacy_peers = {p['id']: p for p in json.loads(r.get_json()['data'])['peers']}
check('legacy /api/ab online enriched', legacy_peers['111']['online'] is True)
fake_execute_query("DELETE FROM computers WHERE uuid='uuid-peer-111'")

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
ok, msg = create_user('user2', 'pass1234')
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
gnames = {g['name'] for g in r.get_json()}
check('groups list (builtin + Default + 2)', r.status_code == 200 and len(r.get_json()) == 5
      and {'Users', 'Administrators', 'Бухгалтерия', 'ИТ-отдел'} <= gnames)
r = client.post('/api/web/groups', json={'name': 'Bad', 'type': 9})
check('group invalid type rejected', r.status_code == 400)

cu = create_user
cu('alice', 'pass1234', None, grp_shared)
cu('bob', 'pass1234', None, grp_shared)
cu('carol', 'pass1234', None, grp_default)
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
check('default uri scheme rustdesk', b'rustdesk://connection/new/' in r.data)
os.environ['RD_URI_SCHEME'] = 'svedesk'
r = anon.get('/ab')
check('custom uri scheme applied', b'svedesk://connection/new/' in r.data)
r = anon.get('/')
check('custom uri scheme on devices page', b'svedesk://connection/new/' in r.data)
os.environ.pop('RD_URI_SCHEME', None)
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
r = anon.put(f'/api/web/clientgen/configs/{cid}', json={'name': 'test-cfg2',
    'config_json': {**cg_valid, 'platform': 'linux', 'permanentPassword': 'Sekret1'}})
check('clientgen set password', r.status_code == 200)
stored = anon.get(f'/api/web/clientgen/configs/{cid}').get_json()
check('clientgen password stored',
      json.loads(stored['config_json']).get('permanentPassword') == 'Sekret1')
r = anon.put(f'/api/web/clientgen/configs/{cid}', json={'name': 'test-cfg2',
    'config_json': {**cg_valid, 'platform': 'linux', 'permanentPassword': ''}})
check('clientgen remove password', r.status_code == 200)
stored = anon.get(f'/api/web/clientgen/configs/{cid}').get_json()
check('clientgen password removed',
      json.loads(stored['config_json']).get('permanentPassword') == '')
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

# ---- refresh при пустом github_run_id (fallback, без GH_TOKEN) ----
fake_execute_query(
    "UPDATE client_configs SET build_status='running', github_run_id='' WHERE id=%s", (cid,))
r = anon.get(f'/api/web/clientgen/configs/{cid}/status')
check('refresh w/o run_id does not crash', r.status_code == 200
      and r.get_json()['build_status'] == 'running')
r = anon.get('/api/web/clientgen/configs')
check('list refreshes running w/o crash', r.status_code == 200)

r = anon.delete(f'/api/web/clientgen/configs/{cid}')
check('clientgen delete', r.status_code == 200)
# non-admin denied
r2 = flask_app.test_client()
r2.post('/api/login', json={'username': 'user2', 'password': 'pass1234'})
r = r2.get('/clientgen')
check('/clientgen denied for non-admin', r.status_code in (302, 403))

# ================= ГРУППЫ: ВСТРОЕННЫЕ, ЧЛЕНСТВО, ИЕРАРХИЯ =================
print('== groups: builtin, membership, hierarchy ==')
groups_list = anon.get('/api/web/groups').get_json()
users_gid = [g for g in groups_list if g['builtin'] == 1][0]['id']
admins_gid = [g for g in groups_list if g['builtin'] == 2][0]['id']
admins_row = [g for g in groups_list if g['id'] == admins_gid][0]
check('builtin Users/Administrators exist', users_gid and admins_gid)
check('Administrators contains Users group', 'Users' in admins_row['members'])
check('admin user in Administrators', 'admin' in admins_row['members'])
r = anon.delete(f'/api/web/groups/{users_gid}')
check('builtin Users delete denied', r.status_code == 400)
r = anon.delete(f'/api/web/groups/{admins_gid}')
check('builtin Administrators delete denied', r.status_code == 400)

r = anon.post('/api/web/groups', json={'name': 'Менеджеры', 'note': 'менеджерский состав'})
check('group create (note)', r.status_code == 201)
gid_m = r.get_json()['id']
r = anon.post('/api/web/groups', json={'name': 'Дирекция'})
check('group create (container)', r.status_code == 201)
gid_d = r.get_json()['id']
r = anon.post('/api/web/groups', json={'name': 'Дирекция'})
check('duplicate group name rejected', r.status_code == 400)

r = anon.post(f'/api/web/groups/{gid_m}/members', json={'member_type': 'user', 'member_id': carol_id})
check('add user to group', r.status_code == 200)
r = anon.post(f'/api/web/groups/{gid_d}/members', json={'member_type': 'group', 'member_id': gid_m})
check('add nested group', r.status_code == 200)
r = anon.post(f'/api/web/groups/{gid_m}/members', json={'member_type': 'group', 'member_id': gid_d})
check('membership cycle rejected', r.status_code == 400)
r = anon.post(f'/api/web/groups/{gid_m}/members', json={'member_type': 'group', 'member_id': gid_m})
check('self-nesting rejected', r.status_code == 400)
members = anon.get(f'/api/web/groups/{gid_m}/members').get_json()
check('members list', any(u['id'] == carol_id for u in members['users']))
r = anon.put(f'/api/web/groups/{gid_m}', json={'note': 'обновлённый комментарий'})
check('group note update', r.status_code == 200)

# Иерархия прав: коллекция, выданная подгруппе, видна участникам контейнера
r = anon.post('/api/web/ab/collections', json={'name': 'Иерархия'})
check('hierarchy collection created', r.status_code == 201)
col_h = [c for c in anon.get('/api/web/ab/collections').get_json() if c['name'] == 'Иерархия'][0]
anon.post('/api/web/ab/rules', json={'collection_id': col_h['id'], 'type': 2, 'to_id': gid_m, 'rule': 1})
r = client.post('/api/ab/shared/profiles', headers=HC)
check('subgroup member sees collection', any(p['guid'] == col_h['guid'] for p in r.get_json()['data']))
anon.post(f'/api/web/groups/{gid_d}/members', json={'member_type': 'user', 'member_id': bob_id})
r = client.post('/api/login', json={'username': 'bob', 'password': 'pass1234', 'uuid': 'uuid-b2', 'id': 'dev-b2'})
HB2 = {'Authorization': 'Bearer ' + r.get_json()['access_token']}
r = client.post('/api/ab/shared/profiles', headers=HB2)
check('container member inherits subgroup rights', any(p['guid'] == col_h['guid'] for p in r.get_json()['data']))
# Обратное направление не действует
r = anon.post('/api/web/ab/collections', json={'name': 'Только дирекция'})
col_d = [c for c in anon.get('/api/web/ab/collections').get_json() if c['name'] == 'Только дирекция'][0]
anon.post('/api/web/ab/rules', json={'collection_id': col_d['id'], 'type': 2, 'to_id': gid_d, 'rule': 1})
r = client.post('/api/ab/shared/profiles', headers=HC)
check('subgroup does not inherit container rights', all(p['guid'] != col_d['guid'] for p in r.get_json()['data']))
r = client.post('/api/ab/shared/profiles', headers=HB2)
check('container member sees container collection', any(p['guid'] == col_d['guid'] for p in r.get_json()['data']))

# ================= ПРАВА АДМИНИСТРАТОРА ЧЕРЕЗ ГРУППУ =================
print('== admin rights via Administrators group ==')
cu('dave', 'pass1234', None, users_gid)
dave_id = fake_execute_query("SELECT id FROM users WHERE username='dave'", fetch_one=True)['id']
anon.post(f'/api/web/groups/{admins_gid}/members', json={'member_type': 'user', 'member_id': dave_id})
dc = flask_app.test_client()
dc.post('/api/login', json={'username': 'dave', 'password': 'pass1234'})
r = dc.get('/api/session/check')
check('Administrators member gets admin session', r.get_json()['role'] == 'admin')
check('Administrators member sees /admin', dc.get('/admin').status_code == 200)
check('Administrators member sees /groups', dc.get('/groups').status_code == 200)
anon.post(f'/api/web/groups/{users_gid}/members', json={'member_type': 'user', 'member_id': carol_id})
cc = flask_app.test_client()
cc.post('/api/login', json={'username': 'carol', 'password': 'pass1234'})
r = cc.get('/api/session/check')
check('Users member does not inherit admin rights', r.get_json()['role'] == 'user')
check('Users member denied /admin', cc.get('/admin').status_code in (301, 302))
check('Users member denied /groups', cc.get('/groups').status_code in (301, 302))

# ================= УСТРОЙСТВА: ОБЛАСТЬ ВИДИМОСТИ =================
print('== devices scope ==')
fake_execute_query("""INSERT INTO computers (id, uuid, hostname, username, os, user_id, group_id)
    VALUES ('dev-c2','uuid-c2','C-PC','carol','Windows / 11', ?, ?)""", (carol_id, users_gid))
r = cc.get('/api/computers')
rows = r.get_json()
check('non-admin sees only own devices', len(rows) == 1 and rows[0]['user_id'] == carol_id)
r = cc.get('/api/stats')
check('non-admin stats scoped', r.get_json()['total_computers'] == 1)
r = anon.get('/api/computers')
check('admin sees all devices', any(c['id'] == 'dev-a1' for c in r.get_json())
      and any(c['id'] == 'dev-c2' for c in r.get_json()))

# ================= LDAP-АУТЕНТИФИКАЦИЯ =================
print('== ldap login ==')
import modules.ldap_auth as ldap_mod  # noqa: E402
ldap_mod.is_enabled = lambda: True
ldap_mod.search_groups = lambda q: [{'name': 'AD-Разработчики',
                                     'dn': 'CN=AD-Разработчики,OU=Groups,DC=asmnet,DC=ru',
                                     'sid': 'S-1-5-21-100'}]


def fake_auth(u, p):
    login = (u.split('@')[0].split('\\')[-1]).lower()
    if login == 'jsmith' and p == 'LdapPass1':
        return {'dn': 'CN=John Smith,OU=Users,DC=asmnet,DC=ru', 'username': 'jsmith',
                'display_name': 'John Smith', 'email': 'jsmith@asmnet.ru',
                'group_sids': ['S-1-5-21-100']}
    return None


ldap_mod.authenticate = fake_auth

r = anon.get('/api/web/ad/groups?search=разраб')
check('ad group search', r.status_code == 200 and len(r.get_json()) == 1)
r = anon.post('/api/web/groups/ad', json={'name': 'AD-Разработчики',
                                          'dn': 'CN=AD-Разработчики,OU=Groups,DC=asmnet,DC=ru',
                                          'sid': 'S-1-5-21-100'})
check('ad group added', r.status_code == 201)
r = anon.get('/api/web/ad/groups?search=разраб')
check('already added ad group excluded', r.get_json() == [])

r = client.post('/api/login', json={'username': 'jsmith@asmnet.ru', 'password': 'LdapPass1',
                                    'uuid': 'uuid-j1', 'id': 'dev-j1'})
check('ldap client login (user@domain)', r.status_code == 200 and r.get_json().get('access_token'))
check('ldap user payload', r.get_json()['user']['name'] == 'jsmith'
      and r.get_json()['user']['is_admin'] is False)
HJ = {'Authorization': 'Bearer ' + r.get_json()['access_token']}
jr = fake_execute_query("SELECT * FROM users WHERE username='jsmith'", fetch_one=True)
check('ldap user provisioned', bool(jr) and jr['auth_source'] == 'ldap' and jr['group_id'] == users_gid)
check('ldap dn stored', jr['ldap_dn'] == 'CN=John Smith,OU=Users,DC=asmnet,DC=ru')
gm = fake_execute_query("""SELECT gm.member_id, g.name FROM group_members gm
    JOIN groups g ON g.id = gm.group_id
    WHERE gm.member_type='user' AND gm.member_id=?""", (jr['id'],), fetch_all=True)
check('ldap user in Users and AD group',
      sorted(x['name'] for x in gm) == ['AD-Разработчики', 'Users'])
r = client.get('/api/user/info', headers=HJ)
check('ldap token works', r.status_code == 200 and r.get_json()['name'] == 'jsmith')

r = client.post('/api/login', json={'username': 'jsmith@asmnet.ru', 'password': 'wrong', 'uuid': 'x'})
check('ldap wrong password rejected', r.status_code == 401)
r = client.post('/api/login', json={'username': 'ASMNET\\jsmith', 'password': 'LdapPass1',
                                    'uuid': 'uuid-j2', 'id': 'dev-j2'})
check('ldap DOMAIN\\user login', r.status_code == 200)
r = client.post('/api/login', json={'username': 'jsmith', 'password': 'LdapPass1'})
check('ldap user cannot login via local form', r.status_code == 401)
r = client.post('/api/login', json={'username': 'unknown@asmnet.ru', 'password': 'LdapPass1', 'uuid': 'x'})
check('unknown domain user rejected', r.status_code == 401)
r = client.post('/api/login', json={'username': 'admin@asmnet.ru', 'password': 'admin', 'uuid': 'x'})
check('local user name collision rejected', r.status_code == 401)

jc = flask_app.test_client()
r = jc.post('/api/login', json={'username': 'jsmith@asmnet.ru', 'password': 'LdapPass1'})
check('ldap web login', r.status_code == 200 and r.get_json()['role'] == 'user')
r = anon.put(f"/api/users/{jr['id']}/password", json={'new_password': '12345'})
check('ldap user password change denied', r.status_code == 403)
ldap_mod.is_enabled = lambda: False
r = client.post('/api/login', json={'username': 'jsmith@asmnet.ru', 'password': 'LdapPass1', 'uuid': 'x'})
check('ldap disabled rejects domain login', r.status_code == 401)
ldap_mod.is_enabled = lambda: True

# ================= КОЛЛЕКЦИИ: СМЕНА ВЛАДЕЛЬЦА, ПРАВА ЗАПИСИ =================
print('== collection owner change ==')
r = cc.post('/api/web/ab/collections', json={'name': 'Кэрол'})
check('carol collection created', r.status_code == 201)
col_c = [c for c in cc.get('/api/web/ab/collections').get_json() if c['name'] == 'Кэрол'][0]
cc.post('/api/web/ab/rules', json={'collection_id': col_c['id'], 'type': 1, 'to_id': bob_id, 'rule': 3})
ac = flask_app.test_client()
ac.post('/api/login', json={'username': 'alice', 'password': 'pass1234'})
r = ac.put(f"/api/web/ab/collections/{col_c['id']}/owner", json={'user_id': alice_id})
check('owner change denied without full rights', r.status_code == 403)
bc = flask_app.test_client()
bc.post('/api/login', json={'username': 'bob', 'password': 'pass1234'})
r = bc.put(f"/api/web/ab/collections/{col_c['id']}/owner", json={'user_id': alice_id})
check('owner change by full-rights user', r.status_code == 200)
col_c2 = [c for c in ac.get('/api/web/ab/collections').get_json() if c['id'] == col_c['id']][0]
check('owner updated', col_c2['owner'] == 'alice' and col_c2['owner_id'] == alice_id)
check('guid rebuilt after owner change', col_c2['guid'] != col_c['guid'])

print('== read-write rights ==')
r = ac.post('/api/web/ab/rules', json={'collection_id': col_c['id'], 'type': 1, 'to_id': carol_id, 'rule': 2})
check('grant read-write', r.status_code == 200)
r = client.post(f"/api/ab/peer/add/{col_c2['guid']}", headers=HC,
                json={'id': 'rw-1', 'hostname': 'RW-PC'})
check('read-write add peer', r.status_code == 200)
r = client.delete(f"/api/ab/peer/{col_c2['guid']}", headers=HC, json=['rw-1'])
check('read-write delete peer', r.status_code == 200, r.data)

# ================= РОЛИ: МИГРАЦИЯ И ЗАЩИТА ПОСЛЕДНЕГО АДМИНА =================
print('== role migration & last admin protection ==')
fake_execute_query("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
fake_execute_query("UPDATE users SET role='admin' WHERE username='dave'")
check('re-init_db with legacy role column', database.init_db())
dave_row = fake_execute_query("SELECT * FROM users WHERE username='dave'", fetch_one=True)
check('role column dropped by migration', 'role' not in dave_row)
gm = fake_execute_query("""SELECT 1 FROM group_members gm JOIN groups g ON g.id=gm.group_id
    WHERE gm.member_type='user' AND gm.member_id=? AND g.builtin=2""", (dave_id,), fetch_one=True)
check('role admin migrated to Administrators', bool(gm))
r = anon.delete(f'/api/web/groups/{admins_gid}/members',
                json={'member_type': 'user', 'member_id': dave_id})
check('second admin removed from Administrators', r.status_code == 200)
admin_id = fake_execute_query("SELECT id FROM users WHERE username='admin'", fetch_one=True)['id']
r = anon.delete(f'/api/web/groups/{admins_gid}/members',
                json={'member_type': 'user', 'member_id': admin_id})
check('last admin cannot be removed from group', r.status_code == 400)
r = anon.delete(f'/api/users/{admin_id}')
check('last admin cannot be deleted', r.status_code == 400)

# ================= РЕДАКТИРОВАНИЕ ПОЛЬЗОВАТЕЛЯ =================
print('== user editing ==')
r = anon.put(f'/api/users/{carol_id}',
             json={'nickname': 'Кэрол Тест', 'email': 'carol@x.ru', 'group_id': users_gid})
check('user edit', r.status_code == 200)
row = fake_execute_query('SELECT * FROM users WHERE id=?', (carol_id,), fetch_one=True)
check('user edit applied', row['nickname'] == 'Кэрол Тест' and row['email'] == 'carol@x.ru'
      and row['group_id'] == users_gid)
r = anon.put(f'/api/users/{bob_id}', json={'username': 'bobby'})
check('local user rename', r.status_code == 200)
r = client.post('/api/login', json={'username': 'bobby', 'password': 'pass1234',
                                    'uuid': 'uuid-b3', 'id': 'dev-b3'})
check('login with new username', r.status_code == 200)
r = anon.put(f'/api/users/{bob_id}', json={'username': 'carol'})
check('rename to existing username rejected', r.status_code == 400)
jr = fake_execute_query("SELECT * FROM users WHERE username='jsmith'", fetch_one=True)
r = anon.put(f"/api/users/{jr['id']}", json={'username': 'jsmith2'})
check('ldap username change rejected', r.status_code == 400)
r = anon.put(f"/api/users/{jr['id']}", json={'nickname': 'John D.', 'email': 'j@asmnet.ru'})
check('ldap other fields editable', r.status_code == 200)

# ---- управление группами пользователя ----
print('== user group memberships ==')
ad_group = fake_execute_query("SELECT * FROM groups WHERE source='ad' LIMIT 1", fetch_one=True)
r = anon.post(f'/api/users/{carol_id}/groups', json={'group_id': gid_d})
check('add user to group via user endpoint', r.status_code == 200)
users_row = anon.get('/api/users').get_json()
carol_api = [u for u in users_row if u['id'] == carol_id][0]
check('membership visible in users list',
      any(m['group_id'] == gid_d for m in carol_api['memberships']))
r = anon.delete(f'/api/users/{carol_id}/groups', json={'group_id': gid_d})
check('remove user from group via user endpoint', r.status_code == 200)
# Локального пользователя можно убрать из AD-группы вручную
r = anon.post(f'/api/users/{bob_id}/groups', json={'group_id': ad_group['id']})
check('add local user to AD group', r.status_code == 200)
r = anon.delete(f'/api/users/{bob_id}/groups', json={'group_id': ad_group['id']})
check('remove local user from AD group', r.status_code == 200)
# Доменные группы пользователя AD не удаляются
r = anon.delete(f'/api/users/{jr["id"]}/groups', json={'group_id': ad_group['id']})
check('AD user cannot be removed from AD group (user endpoint)', r.status_code == 400)
r = anon.delete(f"/api/web/groups/{ad_group['id']}/members",
                json={'member_type': 'user', 'member_id': jr['id']})
check('AD user cannot be removed from AD group (group endpoint)', r.status_code == 400)
# Последний админ защищён и на этом эндпоинте
r = anon.delete(f'/api/users/{admin_id}/groups', json={'group_id': admins_gid})
check('last admin cannot be removed via user endpoint', r.status_code == 400)

# ================= ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ИЗ AD =================
print('== add AD user ==')
ldap_mod.search_users = lambda q: ([{'username': 'aduser1', 'display_name': 'AD User One',
                                     'email': 'aduser1@asmnet.ru',
                                     'dn': 'CN=AD User One,OU=Users,DC=asmnet,DC=ru'}] if q else [])


def fake_lookup(u):
    login = (u.split('@')[0].split('\\')[-1]).lower()
    if login == 'aduser1':
        return {'dn': 'CN=AD User One,OU=Users,DC=asmnet,DC=ru', 'username': 'aduser1',
                'display_name': 'AD User One', 'email': 'aduser1@asmnet.ru',
                'group_sids': ['S-1-5-21-100']}
    return None


ldap_mod.lookup_user = fake_lookup

r = anon.get('/api/web/ad/users?search=aduser')
check('ad user search', r.status_code == 200 and len(r.get_json()) == 1)
r = anon.post('/api/web/ad/users', json={'username': 'aduser1'})
check('ad user added', r.status_code == 201)
au = fake_execute_query("SELECT * FROM users WHERE username='aduser1'", fetch_one=True)
check('ad user provisioned', bool(au) and au['auth_source'] == 'ldap'
      and au['group_id'] == users_gid and au['nickname'] == 'AD User One')
gm = fake_execute_query("""SELECT g.name FROM group_members gm JOIN groups g ON g.id=gm.group_id
    WHERE gm.member_type='user' AND gm.member_id=?""", (au['id'],), fetch_all=True)
check('ad user memberships synced (Users + AD group)',
      sorted(x['name'] for x in gm) == ['AD-Разработчики', 'Users'])
r = anon.post('/api/web/ad/users', json={'username': 'aduser1'})
check('duplicate ad user rejected', r.status_code == 400)
r = anon.get('/api/web/ad/users?search=aduser')
check('added ad user excluded from search', r.get_json() == [])
r = anon.post('/api/web/ad/users', json={'username': 'ghost'})
check('unknown ad user rejected', r.status_code == 404)

print(f'\nALL {passed} CHECKS PASSED')
