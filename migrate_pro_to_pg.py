# Перенос данных из sqlite3 rustdesk-server-pro в PostgreSQL rustdesk-api.
# Запуск:  python migrate_pro_to_pg.py <path/to/db.sqlite3> <DSN>
# Пример:  python migrate_pro_to_pg.py /tmp/db.sqlite3 \
#             postgresql://rustdesk:rustdesk@db:5432/rustdesk_monitor
import sqlite3, sys, base64, json, uuid as uuidlib
import psycopg2

def s(x):
    """sqlite value -> строка (bytes декодируем, int/str приводим, None->'')"""
    if x is None: return ''
    if isinstance(x, bytes): return x.decode('utf8', 'replace')
    return str(x)

def b2u(b):
    if not b: return None
    try: return str(uuidlib.UUID(bytes=b))
    except Exception: return None

def b64(b):
    return base64.b64encode(b).decode() if b else ''

def jget(info, key, default=''):
    if not info: return default
    if isinstance(info, dict): return info.get(key, default) or default
    try:
        o = json.loads(info)
        return (o.get(key, default) if isinstance(o, dict) else default) or default
    except Exception: return default

def ts(v):
    if not v: return None
    t = s(v)
    if '.' in t:
        head, frac = t.split('.', 1)
        t = head + '.' + frac[:6]
    return t

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, dsn = sys.argv[1], sys.argv[2]
    sc = sqlite3.connect(src)          # text_factory по умолчанию: TEXT->str, BLOB->bytes
    cur = sc.cursor()
    pg = psycopg2.connect(dsn); pg.autocommit = False
    p = pg.cursor()
    def q(sql): return cur.execute(sql).fetchall()

    # ---- группы ----
    grp_map = {}
    for guid, name, gtype in q("SELECT guid,name,type FROM grp"):
        p.execute("INSERT INTO groups (name,type) VALUES (%s,%s) RETURNING id",
                  (s(name), 2 if gtype == 2 else 1))
        grp_map[guid] = p.fetchone()[0]
    print('groups:', len(grp_map))

    # ---- пользователи ----
    user_map = {}
    for guid, name, email, role, grp, status, password, display_name in q(
        "SELECT guid,name,email,role,grp,status,password,display_name FROM user"):
        uname = s(name)
        p.execute("SELECT id FROM users WHERE username=%s", (uname,))
        ex = p.fetchone()
        if ex:
            user_map[guid] = ex[0]; continue
        p.execute("""INSERT INTO users (username,password_hash,role,email,group_id,status,nickname)
                     VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                  (uname, s(password), 'admin' if role == 1 else 'user',
                   s(email) or None, grp_map.get(grp, 1), status or 1, s(display_name) or None))
        user_map[guid] = p.fetchone()[0]
    print('users:', len(user_map))

    # ---- устройства (peer -> computers) ----
    peer_id_by_guid = {}
    n = 0
    for guid, pid, puuid, puser, pgrp, status, last_online, info in q(
        "SELECT guid,id,uuid,user,grp,status,last_online,info FROM peer"):
        info = s(info)
        uid = user_map.get(puser)
        gid = grp_map.get(pgrp) if pgrp else None
        if not gid and uid:
            p.execute("SELECT group_id FROM users WHERE id=%s", (uid,))
            r = p.fetchone(); gid = r[0] if r else 1
        p.execute("""INSERT INTO computers (id,uuid,hostname,username,os,cpu,memory,version,
                     user_id,group_id,last_online,last_online_timestamp)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)""",
                  (s(pid), b64(puuid), jget(info,'hostname'), jget(info,'username'),
                   jget(info,'os'), jget(info,'cpu'), jget(info,'memory'), jget(info,'version'),
                   uid, gid or 1, ts(last_online)))
        peer_id_by_guid[guid] = s(pid); n += 1
    print('computers:', n)

    # ---- адресные книги ----
    ab_map = {}
    for guid, name, owner, personal in q("SELECT guid,name,owner,personal FROM ab"):
        owner_id = user_map.get(owner)
        if not owner_id: continue
        if personal == 1:
            ab_map[guid] = (owner_id, 0)
        else:
            p.execute("INSERT INTO address_book_collections (user_id,name) VALUES (%s,%s) RETURNING id",
                      (owner_id, s(name)))
            ab_map[guid] = (owner_id, p.fetchone()[0])
    print('address books:', len(ab_map))

    # ---- записи адресной книги ----
    n = 0
    for ab_guid, peer_guid, ab_id, info in q("SELECT ab,peer,id,info FROM ab_peer"):
        if ab_guid not in ab_map: continue
        owner_id, coll_id = ab_map[ab_guid]
        info = s(info)
        rust_id = s(ab_id) if ab_id else peer_id_by_guid.get(peer_guid)
        if not rust_id: continue
        p.execute("SELECT hostname,username,os FROM computers WHERE id=%s", (rust_id,))
        row = p.fetchone()
        p.execute("""INSERT INTO address_books (id,username,hostname,alias,platform,hash,user_id,collection_id)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (rust_id,
                   (row and row[1]) or jget(info,'username'),
                   (row and row[0]) or jget(info,'hostname'),
                   jget(info,'alias'), (row and row[2]) or '',
                   jget(info,'hash'), owner_id, coll_id))
        n += 1
    print('address book peers:', n)

    # ---- теги ----
    n = 0
    for ab_guid, name, color in q("SELECT ab,name,color FROM ab_tag"):
        if ab_guid not in ab_map: continue
        owner_id, coll_id = ab_map[ab_guid]
        p.execute("INSERT INTO tags (name,user_id,color,collection_id) VALUES (%s,%s,%s,%s)",
                  (s(name), owner_id, color or 0, coll_id)); n += 1
    print('tags:', n)

    # ---- правила доступа ----
    n = 0
    for ab_guid, ruser, rgrp, rule in q("SELECT ab,user,grp,rule FROM ab_rule"):
        if ab_guid not in ab_map: continue
        owner_id, coll_id = ab_map[ab_guid]
        if ruser:
            p.execute("""INSERT INTO address_book_collection_rules
                         (user_id,collection_id,rule,type,to_id) VALUES (%s,%s,%s,1,%s)""",
                      (owner_id, coll_id, rule, user_map.get(ruser)))
        elif rgrp:
            p.execute("""INSERT INTO address_book_collection_rules
                         (user_id,collection_id,rule,type,to_id) VALUES (%s,%s,%s,2,%s)""",
                      (owner_id, coll_id, rule, grp_map.get(rgrp)))
        n += 1
    print('rules:', n)

    # ---- аудит соединений ----
    n = 0
    for typ, remote, local, created, end, uname in q(
        "SELECT type,remote,local,created_at,end_time,user_name FROM audit_conn"):
        p.execute("""INSERT INTO rustdesk_audits (audit_type,device_id,from_peer,from_name,conn_type,created_at,close_time)
                     VALUES ('conn',%s,%s,%s,%s,%s,%s)""",
                  (peer_id_by_guid.get(remote), peer_id_by_guid.get(local),
                   s(uname), typ, ts(created), ts(end))); n += 1
    print('audit conn:', n)

    # ---- аудит файлов ----
    n = 0
    for typ, remote, local, created, path, is_file, info in q(
        "SELECT type,remote,local,created_at,path,is_file,info FROM audit_file"):
        p.execute("""INSERT INTO rustdesk_audits (audit_type,device_id,from_peer,file_type,path,is_file,info,created_at)
                     VALUES ('file',%s,%s,%s,%s,%s,%s,%s)""",
                  (peer_id_by_guid.get(remote), peer_id_by_guid.get(local), typ,
                   s(path), bool(is_file), s(info), ts(created))); n += 1
    print('audit file:', n)

    pg.commit()
    print('DONE')

if __name__ == '__main__':
    main()
