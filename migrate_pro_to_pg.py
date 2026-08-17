# Перенос данных из sqlite3 rustdesk-server-pro в PostgreSQL rustdesk-api.
# Запуск:  python migrate_pro_to_pg.py <path/to/db.sqlite3> <DSN>
# Пример:  python migrate_pro_to_pg.py ../mht-rustdesk-server/db.sqlite3 \
#             postgresql://rustdesk:rustdesk@localhost:5432/rustdesk_monitor
import sqlite3, sys, base64, json, uuid as uuidlib
import psycopg2

def b2u(b):
    """binary uuid -> строка uuid"""
    if not b: return None
    try: return str(uuidlib.UUID(bytes=b))
    except Exception: return None

def b64(b):
    if not b: return ''
    return base64.b64encode(b).decode()

def jget(info, key, default=''):
    if not info: return default
    if isinstance(info, dict): return info.get(key, default)
    try:
        o = json.loads(info)
        return o.get(key, default) if isinstance(o, dict) else default
    except Exception: return default

def ts(s):
    """sqlite timestamp (возможно с наносекундами) -> строка, приемлемая для PG"""
    if not s: return None
    s = s.decode('utf8','replace') if isinstance(s, bytes) else str(s)
    # усечь дробную часть до 6 знаков
    if '.' in s:
        head, frac = s.split('.', 1)
        s = head + '.' + frac[:6]
    return s

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, dsn = sys.argv[1], sys.argv[2]
    sc = sqlite3.connect(src); sc.text_factory = bytes
    cur = sc.cursor()
    pg = psycopg2.connect(dsn); pg.autocommit = False
    p = pg.cursor()

    def q(sql): return cur.execute(sql).fetchall()

    # ---- группы ----
    grp_map = {}
    for guid, name, gtype in q("SELECT guid,name,type FROM grp"):
        p.execute("INSERT INTO groups (name,type) VALUES (%s,%s) RETURNING id",
                  ((name or b'').decode('utf8','replace'), 2 if gtype == 2 else 1))
        grp_map[guid] = p.fetchone()[0]
    print('groups:', len(grp_map))

    # ---- пользователи ----
    user_map = {}
    for guid, name, email, role, grp, status, password, display_name in q(
        "SELECT guid,name,email,role,grp,status,password,display_name FROM user"):
        uname = (name or b'').decode('utf8','replace')
        p.execute("SELECT id FROM users WHERE username=%s", (uname,))
        existing = p.fetchone()
        if existing:
            user_map[guid] = existing[0]; continue
        p.execute("""INSERT INTO users (username,password_hash,role,email,group_id,status,nickname)
                     VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                  (uname,
                   (password or b'').decode('utf8','replace'),
                   'admin' if role == 1 else 'user',
                   (email or b'').decode('utf8','replace') or None,
                   grp_map.get(grp, 1), status or 1,
                   (display_name or b'').decode('utf8','replace') or None))
        user_map[guid] = p.fetchone()[0]
    print('users:', len(user_map))

    # ---- устройства (peer -> computers) ----
    peer_id_by_guid = {}
    n = 0
    for guid, pid, puuid, puser, pgrp, status, last_online, info in q(
        "SELECT guid,id,uuid,user,grp,status,last_online,info FROM peer"):
        info = (info or b'').decode('utf8','replace')
        uid = user_map.get(puser)
        gid = grp_map.get(pgrp) if pgrp else None
        if not gid and uid:
            p.execute("SELECT group_id FROM users WHERE id=%s", (uid,))
            r = p.fetchone(); gid = r[0] if r else 1
        p.execute("""INSERT INTO computers (id,uuid,hostname,username,os,cpu,memory,version,
                     user_id,group_id,last_online,last_online_timestamp)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)""",
                  (str(pid), b64(puuid),
                   jget(info,'hostname'), jget(info,'username'), jget(info,'os'),
                   jget(info,'cpu'), jget(info,'memory'), jget(info,'version'),
                   uid, gid or 1, ts(last_online)))
        peer_id_by_guid[guid] = str(pid); n += 1
    print('computers:', n)

    # ---- адресные книги ----
    ab_map = {}  # ab guid -> (owner_user_id, collection_id)
    for guid, name, owner, personal in q("SELECT guid,name,owner,personal FROM ab"):
        owner_id = user_map.get(owner)
        if not owner_id: continue
        nm = (name or b'').decode('utf8','replace')
        if personal == 1:
            ab_map[guid] = (owner_id, 0)
        else:
            p.execute("INSERT INTO address_book_collections (user_id,name) VALUES (%s,%s) RETURNING id",
                      (owner_id, nm))
            ab_map[guid] = (owner_id, p.fetchone()[0])
    print('address books:', len(ab_map))

    # ---- записи адресной книги ----
    n = 0
    for ab_guid, peer_guid, ab_id, info in q("SELECT ab,peer,id,info FROM ab_peer"):
        if ab_guid not in ab_map: continue
        owner_id, coll_id = ab_map[ab_guid]
        info = (info or b'').decode('utf8','replace')
        rust_id = str(ab_id) if ab_id else peer_id_by_guid.get(peer_guid)
        if not rust_id: continue
        # данные пира из computers
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
                  ((name or b'').decode('utf8','replace'), owner_id, color or 0, coll_id)); n += 1
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
                   (uname or b'').decode('utf8','replace'), typ, ts(created), ts(end))); n += 1
    print('audit conn:', n)

    # ---- аудит файлов ----
    n = 0
    for typ, remote, local, created, path, is_file, info in q(
        "SELECT type,remote,local,created_at,path,is_file,info FROM audit_file"):
        p.execute("""INSERT INTO rustdesk_audits (audit_type,device_id,from_peer,file_type,path,is_file,info,created_at)
                     VALUES ('file',%s,%s,%s,%s,%s,%s,%s)""",
                  (peer_id_by_guid.get(remote), peer_id_by_guid.get(local), typ,
                   (path or b'').decode('utf8','replace'), bool(is_file),
                   (info or b'').decode('utf8','replace'), ts(created))); n += 1
    print('audit file:', n)

    pg.commit()
    print('DONE')

if __name__ == '__main__':
    main()
