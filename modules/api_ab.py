import json
import time

from flask import request, jsonify, session

from modules import ab
from modules import groups as gr
from modules.database import execute_query
from modules.auth import add_audit_log, is_session_expired


def _bearer_token():
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:].strip()
    return ''


def get_auth_user():
    """Определяет пользователя по Bearer-токену (клиент RustDesk) или по сессии (веб-UI)"""
    token = _bearer_token()
    if token:
        user, ut = ab.get_user_by_access_token(token)
        if user and ab.is_user_enabled(user):
            ab.auto_refresh_token(ut)
            return user
        return None
    if 'user_id' in session and not is_session_expired():
        user = execute_query('SELECT * FROM users WHERE id = %s', (session['user_id'],), fetch_one=True)
        if user and ab.is_user_enabled(user):
            return user
    return None


def _error(message, code=400):
    return jsonify({'error': message}), code


def _parse_json_body():
    try:
        return request.get_json(force=True, silent=False)
    except Exception:
        return None


def init_ab_routes(app):

    # ========== КЛИЕНТСКАЯ АУТЕНТИФИКАЦИЯ ==========

    @app.route('/api/login-options', methods=['GET'])
    def login_options():
        return jsonify([])

    @app.route('/api/user/info', methods=['GET'])
    @app.route('/api/currentUser', methods=['POST', 'GET'])
    def user_info():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        return jsonify(ab.user_payload(user))

    @app.route('/api/logout', methods=['POST'])
    def client_logout():
        token = _bearer_token()
        if token:
            user, ut = ab.get_user_by_access_token(token)
            if user:
                if ut and ut.get('device_uuid'):
                    ab.unbind_device_user(ut['device_uuid'], user['id'])
                ab.delete_user_token(user['id'], token)
                add_audit_log(user['id'], 'CLIENT_LOGOUT', user['username'],
                              'RustDesk client logout', request.remote_addr)
        return app.response_class('null', mimetype='application/json')

    # ========== ГРУППА: ПОЛЬЗОВАТЕЛИ И УСТРОЙСТВА ==========

    @app.route('/api/peers', methods=['GET'])
    def group_peers():
        """Устройства доступных пользователей (вкладка "Группа" в клиенте)"""
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        if ab.can_see_group_members(user):
            users = ab.list_users_by_group(user.get('group_id', 1))
        else:
            users = [user]

        names_by_id = {u['id']: u['username'] for u in users}
        user_ids = list(names_by_id.keys())
        now_ts = int(time.time())
        data = []
        for row in ab.list_computers_by_user_ids(user_ids):
            online = (row.get('last_online_timestamp') or 0) > now_ts - 35
            data.append({
                'id': row.get('id') or '',
                'info': {
                    'device_name': row.get('hostname') or '',
                    'os': row.get('os') or '',
                    'username': row.get('username') or '',
                },
                'status': 1 if online else None,
                'user': '',
                'user_name': names_by_id.get(row.get('user_id'), ''),
                'note': '',
                'device_group_name': '',
            })
        return jsonify({'total': len(data), 'data': data})

    @app.route('/api/device-group/accessible', methods=['GET'])
    def device_group_accessible():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        return jsonify({'total': 0, 'data': []})

    # ========== LEGACY АДРЕСНАЯ КНИГА ==========

    @app.route('/api/ab', methods=['GET'])
    def get_ab():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        rows = ab.list_ab(user['id'], 0, page=1, page_size=100000)
        tags = ab.list_tags(user['id'], 0)
        tag_names = [t['name'] for t in tags]
        tag_colors = {t['name']: t['color'] for t in tags}

        peers = [ab.ab_to_payload(r) for r in rows]
        ab.enrich_peers_online(peers)
        res = {
            'peers': peers,
            'tags': tag_names,
            'tag_colors': json.dumps(tag_colors, ensure_ascii=False),
        }
        return jsonify({'data': json.dumps(res, ensure_ascii=False)})

    @app.route('/api/ab', methods=['POST'])
    def update_ab():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body or 'data' not in body:
            return _error('ParamsError')
        try:
            data = json.loads(body['data'])
            tag_colors = json.loads(data.get('tag_colors') or '{}')
        except (json.JSONDecodeError, TypeError) as e:
            return _error(f'ParamsError{e}')

        try:
            ab.sync_address_book(data.get('peers') or [], user['id'], 0)
            ab.sync_tags(user['id'], tag_colors, 0)
        except Exception as e:
            return _error(f'OperationFailed{e}')
        return app.response_class('null', mimetype='application/json')

    # ========== PERSONAL АДРЕСНАЯ КНИГА ==========

    @app.route('/api/ab/personal', methods=['POST'])
    def ab_personal():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        if ab.AB_PERSONAL == 1:
            return jsonify({
                'guid': ab.personal_guid(user),
                'name': user['username'],
                'rule': ab.RULE_FULL_CONTROL,
            })
        return app.response_class('null', mimetype='application/json')

    @app.route('/api/ab/settings', methods=['POST'])
    def ab_settings():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        return jsonify({'max_peer_one_ab': 0})

    @app.route('/api/ab/shared/profiles', methods=['POST'])
    def ab_shared_profiles():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        res = []
        for c in ab.list_collections_by_user(user['id']):
            res.append({
                'guid': ab.compose_guid(user.get('group_id', 1), user['id'], c['id']),
                'name': c['name'],
                'owner': user['username'],
                'note': '',
                'rule': ab.RULE_FULL_CONTROL,
            })

        all_ab_ids = {}
        for rule in ab.collection_read_rules(user):
            cid = rule['collection_id']
            if cid in all_ab_ids:
                if all_ab_ids[cid] < rule['rule']:
                    all_ab_ids[cid] = rule['rule']
            else:
                all_ab_ids[cid] = rule['rule']

        collections = ab.list_collections_by_ids(list(all_ab_ids.keys()))
        for c in collections:
            owner = execute_query('SELECT * FROM users WHERE id = %s', (c['user_id'],), fetch_one=True)
            if not owner:
                continue
            res.append({
                'guid': ab.compose_guid(owner.get('group_id', 1), owner['id'], c['id']),
                'name': c['name'],
                'owner': owner['username'],
                'note': '',
                'rule': all_ab_ids.get(c['id'], 0),
            })

        return jsonify({'total': 0, 'data': res})

    @app.route('/api/ab/peers', methods=['POST'])
    def ab_peers():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        guid = request.args.get('ab') or ab.personal_guid(user)
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_read_privilege(user, uid, cid):
            return _error('NoAccess')

        current = request.args.get('current', 1, type=int)
        page_size = request.args.get('pageSize', 100, type=int)
        rows = ab.list_ab(uid, cid, page=current, page_size=page_size)
        data = [ab.ab_to_payload(r) for r in rows]
        ab.enrich_peers_online(data)
        return jsonify({
            'total': ab.count_ab(uid, cid),
            'data': data,
            'licensed_devices': 99999,
        })

    @app.route('/api/ab/tags/<guid>', methods=['POST'])
    def ab_tags(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_read_privilege(user, uid, cid):
            return _error('NoAccess')
        tags = ab.list_tags(uid, cid)
        return jsonify([ab.tag_to_payload(t) for t in tags])

    @app.route('/api/ab/peer/add/<guid>', methods=['POST'])
    def ab_peer_add(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body:
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        try:
            ab.add_ab(body, uid, cid)
        except Exception as e:
            return _error(f'OperationFailed{e}')
        return '', 200

    @app.route('/api/ab/peer/<guid>', methods=['DELETE'])
    def ab_peer_del(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        ids = _parse_json_body()
        if not isinstance(ids, list):
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        for peer_id in ids:
            row = ab.ab_info(uid, peer_id, cid)
            if not row:
                return _error('ItemNotFound')
            ab.delete_ab(row['row_id'])
        return '', 200

    @app.route('/api/ab/peer/update/<guid>', methods=['PUT'])
    def ab_peer_update(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body or 'id' not in body:
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        row = ab.ab_info(uid, body['id'], cid)
        if not row:
            return _error('ItemNotFound')

        # Поля, которые клиент RustDesk 1.4.x обновляет через этот эндпоинт
        # (alias, tags, note, password/hash + syncFromRecent: username/hostname/platform)
        updatable = ('password', 'hash', 'tags', 'alias', 'note',
                     'username', 'hostname', 'platform', 'device_group_name')
        allowed = {k: v for k, v in body.items() if k in updatable}
        try:
            ab.update_ab_by_map(row['row_id'], allowed)
        except Exception as e:
            return _error(f'OperationFailed{e}')
        return '', 200

    # ========== ТЕГИ ==========

    @app.route('/api/ab/tag/add/<guid>', methods=['POST'])
    def ab_tag_add(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body or not body.get('name'):
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        if ab.tag_info(uid, body['name'], cid):
            return _error('ItemExists')
        try:
            ab.add_tag(uid, body['name'], cid, body.get('color', 0))
        except Exception as e:
            return _error(f'OperationFailed{e}')
        return '', 200

    @app.route('/api/ab/tag/rename/<guid>', methods=['PUT'])
    def ab_tag_rename(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body or 'old' not in body or 'new' not in body:
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        tag = ab.tag_info(uid, body['old'], cid)
        if not tag:
            return _error('ItemNotFound')
        if ab.tag_info(uid, body['new'], cid):
            return _error('ItemExists')
        ab.update_tag(tag['id'], name=body['new'])
        return '', 200

    @app.route('/api/ab/tag/update/<guid>', methods=['PUT'])
    def ab_tag_update(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        body = _parse_json_body()
        if not body or 'name' not in body:
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        tag = ab.tag_info(uid, body['name'], cid)
        if not tag:
            return _error('ItemNotFound')
        ab.update_tag(tag['id'], color=body.get('color', tag['color']))
        return '', 200

    @app.route('/api/ab/tag/<guid>', methods=['DELETE'])
    def ab_tag_del(guid):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        names = _parse_json_body()
        if not isinstance(names, list):
            return _error('ParamsError')
        _, uid, cid, err = ab.check_guid(user, guid)
        if err:
            return _error(err)
        if not ab.check_write_privilege(user, uid, cid):
            return _error('NoAccess')

        for name in names:
            tag = ab.tag_info(uid, name, cid)
            if not tag:
                return _error('ItemNotFound')
            ab.delete_tag(tag['id'])
        return '', 200

    # ========== ВЕБ-UI: КОЛЛЕКЦИИ И ПРАВИЛА ==========

    def _can_manage_collection(user, c):
        """Полные права на коллекцию: владелец, полные права по правилам или админ"""
        return gr.is_admin_user(user) or ab.check_full_control_privilege(user, c['user_id'], c['id'])

    @app.route('/api/web/ab/collections', methods=['GET'])
    def web_ab_collections():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)

        res = []
        for c in ab.list_collections_by_user(user['id']):
            res.append({
                'id': c['id'],
                'name': c['name'],
                'guid': ab.compose_guid(user.get('group_id', 1), user['id'], c['id']),
                'owner': user['username'],
                'owner_id': user['id'],
                'rule': ab.RULE_FULL_CONTROL,
                'own': True,
            })
        own_ids = {item['id'] for item in res}
        seen = {}
        for rule in ab.collection_read_rules(user):
            cid = rule['collection_id']
            if cid in own_ids:
                continue
            if cid not in seen or seen[cid]['rule'] < rule['rule']:
                seen[cid] = rule
        for cid, rule in seen.items():
            c = ab.collection_info_by_id(cid)
            if not c:
                continue
            owner = execute_query('SELECT * FROM users WHERE id = %s', (c['user_id'],), fetch_one=True)
            if not owner:
                continue
            res.append({
                'id': c['id'],
                'name': c['name'],
                'guid': ab.compose_guid(owner.get('group_id', 1), owner['id'], c['id']),
                'owner': owner['username'],
                'owner_id': owner['id'],
                'rule': rule['rule'],
                'own': False,
            })
        return jsonify(res)

    @app.route('/api/web/ab/collections', methods=['POST'])
    def web_ab_collection_create():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        body = _parse_json_body()
        name = (body or {}).get('name', '').strip()
        if not name:
            return _error('ParamsError')
        ab.create_collection(user['id'], name)
        add_audit_log(user['id'], 'AB_CREATE_COLLECTION', name, 'Address book collection created', request.remote_addr)
        return jsonify({'message': 'Collection created'}), 201

    @app.route('/api/web/ab/collections/<int:collection_id>', methods=['PUT'])
    def web_ab_collection_rename(collection_id):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        c = ab.collection_info_by_id(collection_id)
        if not c:
            return _error('ItemNotFound', 404)
        if not _can_manage_collection(user, c):
            return _error('NoAccess', 403)
        body = _parse_json_body()
        name = (body or {}).get('name', '').strip()
        if not name:
            return _error('ParamsError')
        ab.rename_collection(collection_id, name)
        return jsonify({'message': 'Collection renamed'})

    @app.route('/api/web/ab/collections/<int:collection_id>', methods=['DELETE'])
    def web_ab_collection_delete(collection_id):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        c = ab.collection_info_by_id(collection_id)
        if not c:
            return _error('ItemNotFound', 404)
        if not _can_manage_collection(user, c):
            return _error('NoAccess', 403)
        ab.delete_collection(collection_id)
        add_audit_log(user['id'], 'AB_DELETE_COLLECTION', str(collection_id), 'Address book collection deleted', request.remote_addr)
        return jsonify({'message': 'Collection deleted'})

    @app.route('/api/web/ab/collections/<int:collection_id>/owner', methods=['PUT'])
    def web_ab_collection_change_owner(collection_id):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        c = ab.collection_info_by_id(collection_id)
        if not c:
            return _error('ItemNotFound', 404)
        if not _can_manage_collection(user, c):
            return _error('NoAccess', 403)
        body = _parse_json_body() or {}
        new_owner_id = body.get('user_id')
        new_owner = execute_query('SELECT * FROM users WHERE id = %s', (new_owner_id,), fetch_one=True)
        if not new_owner:
            return _error('ItemNotFound')
        execute_query(
            'UPDATE address_book_collections SET user_id = %s, updated_at = NOW() WHERE id = %s',
            (new_owner_id, collection_id))
        add_audit_log(user['id'], 'AB_CHANGE_OWNER', str(collection_id),
                      f'Collection owner changed to {new_owner["username"]}', request.remote_addr)
        return jsonify({'message': 'Owner changed'})

    @app.route('/api/web/ab/rules/<int:collection_id>', methods=['GET'])
    def web_ab_rules(collection_id):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        c = ab.collection_info_by_id(collection_id)
        if not c:
            return _error('ItemNotFound', 404)
        if not _can_manage_collection(user, c):
            return _error('NoAccess', 403)

        res = []
        for r in ab.list_rules_by_collection(collection_id):
            target_name = ''
            if r['type'] == ab.RULE_TYPE_PERSONAL:
                u = execute_query('SELECT username FROM users WHERE id = %s', (r['to_id'],), fetch_one=True)
                target_name = u['username'] if u else ''
            else:
                g = gr.group_info_by_id(r['to_id'])
                target_name = g['name'] if g else f"Группа {r['to_id']}"
            res.append({
                'id': r['id'],
                'collection_id': r['collection_id'],
                'rule': r['rule'],
                'type': r['type'],
                'to_id': r['to_id'],
                'target_name': target_name,
            })
        return jsonify(res)

    @app.route('/api/web/ab/rules', methods=['POST'])
    def web_ab_rule_add():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        body = _parse_json_body() or {}
        collection_id = body.get('collection_id')
        rule = body.get('rule')
        rule_type = body.get('type', ab.RULE_TYPE_PERSONAL)
        to_id = body.get('to_id')

        if not collection_id or rule not in (1, 2, 3) or rule_type not in (1, 2) or not to_id:
            return _error('ParamsError')

        c = ab.collection_info_by_id(collection_id)
        if not c:
            return _error('ItemNotFound', 404)
        if not _can_manage_collection(user, c):
            return _error('NoAccess', 403)

        if rule_type == ab.RULE_TYPE_PERSONAL:
            target = execute_query('SELECT id FROM users WHERE id = %s', (to_id,), fetch_one=True)
            if not target:
                return _error('ItemNotFound')
        else:
            if not gr.group_info_by_id(to_id):
                return _error('ItemNotFound')

        existing = execute_query("""
            SELECT id FROM address_book_collection_rules
            WHERE collection_id = %s AND type = %s AND to_id = %s
        """, (collection_id, rule_type, to_id), fetch_one=True)
        if existing:
            ab.update_rule(existing['id'], rule)
        else:
            ab.add_rule(user['id'], collection_id, rule, rule_type, to_id)
        return jsonify({'message': 'Rule saved'}), 200

    @app.route('/api/web/ab/rules/<int:rule_id>', methods=['DELETE'])
    def web_ab_rule_delete(rule_id):
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        r = ab.rule_info_by_id(rule_id)
        if not r:
            return _error('ItemNotFound', 404)
        c = ab.collection_info_by_id(r['collection_id'])
        if not c or not _can_manage_collection(user, c):
            return _error('NoAccess', 403)
        ab.delete_rule(rule_id)
        return jsonify({'message': 'Rule deleted'})

    @app.route('/api/web/ab/users', methods=['GET'])
    def web_ab_users():
        """Пользователи для настройки общего доступа"""
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        users = execute_query(
            'SELECT id, username FROM users ORDER BY username', fetch_all=True) or []
        return jsonify([{'id': u['id'], 'username': u['username']} for u in users])

    # ========== ВЕБ-UI: ГРУППЫ ПОЛЬЗОВАТЕЛЕЙ ==========

    @app.route('/api/web/groups', methods=['GET'])
    def web_groups_list():
        user = get_auth_user()
        if not user:
            return _error('Unauthorized', 401)
        return jsonify([{
            'id': g['id'], 'name': g['name'], 'type': g['type'],
            'source': g.get('source') or 'local', 'note': g.get('note') or '',
            'builtin': g.get('builtin') or 0,
            'members': gr.member_names(g['id']),
        } for g in gr.list_groups()])

    @app.route('/api/web/groups', methods=['POST'])
    def web_group_create():
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        body = _parse_json_body() or {}
        name = (body.get('name') or '').strip()
        note = (body.get('note') or '').strip()
        gtype = body.get('type', gr.GROUP_TYPE_DEFAULT)
        if not name:
            return _error('ParamsError')
        if gtype not in (gr.GROUP_TYPE_DEFAULT, gr.GROUP_TYPE_SHARE):
            return _error('ParamsError')
        if gr.group_name_exists(name):
            return _error('GroupNameExists')
        gid = gr.create_group(name, note=note, group_type=gtype)
        add_audit_log(user['id'], 'CREATE_GROUP', name, 'Group created', request.remote_addr)
        return jsonify({'message': 'Group created', 'id': gid}), 201

    @app.route('/api/web/groups/<int:group_id>', methods=['PUT'])
    def web_group_update(group_id):
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        g = gr.group_info_by_id(group_id)
        if not g:
            return _error('ItemNotFound', 404)
        body = _parse_json_body() or {}
        name = body.get('name')
        note = body.get('note')
        gtype = body.get('type')
        if name is not None:
            name = str(name).strip()
            if not name or gr.group_name_exists(name, exclude_id=group_id):
                return _error('ParamsError' if not name else 'GroupNameExists')
        if gtype is not None and gtype not in (gr.GROUP_TYPE_DEFAULT, gr.GROUP_TYPE_SHARE):
            return _error('ParamsError')
        gr.update_group(group_id, name=name, note=str(note).strip() if note is not None else None,
                        group_type=gtype)
        return jsonify({'message': 'Group updated'})

    @app.route('/api/web/groups/<int:group_id>', methods=['DELETE'])
    def web_group_delete(group_id):
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        g = gr.group_info_by_id(group_id)
        if not g:
            return _error('ItemNotFound', 404)
        if g.get('builtin', 0) != gr.BUILTIN_NONE or group_id == 1:
            return _error('Cannot delete builtin group')
        gr.delete_group(group_id)
        add_audit_log(user['id'], 'DELETE_GROUP', g['name'], 'Group deleted', request.remote_addr)
        return jsonify({'message': 'Group deleted'})

    @app.route('/api/web/groups/<int:group_id>/members', methods=['GET'])
    def web_group_members(group_id):
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        if not gr.group_info_by_id(group_id):
            return _error('ItemNotFound', 404)
        return jsonify(gr.list_members(group_id))

    @app.route('/api/web/groups/<int:group_id>/members', methods=['POST'])
    def web_group_member_add(group_id):
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        body = _parse_json_body() or {}
        ok, err = gr.add_member(group_id, body.get('member_type'), body.get('member_id'))
        if not ok:
            code = 404 if err in ('GroupNotFound', 'UserNotFound') else 400
            return _error(err or 'ParamsError', code)
        return jsonify({'message': 'Member added'})

    @app.route('/api/web/groups/<int:group_id>/members', methods=['DELETE'])
    def web_group_member_remove(group_id):
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        body = _parse_json_body() or {}
        if body.get('member_type') not in (gr.MEMBER_USER, gr.MEMBER_GROUP) or not body.get('member_id'):
            return _error('ParamsError')
        g = gr.group_info_by_id(group_id)
        if g and g.get('builtin') == gr.BUILTIN_ADMINS \
                and body['member_type'] == gr.MEMBER_USER \
                and gr.is_last_admin_user(body['member_id']):
            return _error('Cannot remove the last administrator')
        gr.remove_member(group_id, body['member_type'], body['member_id'])
        return jsonify({'message': 'Member removed'})

    @app.route('/api/web/groups/ad', methods=['POST'])
    def web_group_add_ad():
        """Добавить группу из AD (результат поиска в каталоге)"""
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        body = _parse_json_body() or {}
        name = (body.get('name') or '').strip()
        dn = (body.get('dn') or '').strip()
        sid = (body.get('sid') or '').strip()
        if not name or not dn:
            return _error('ParamsError')
        existing = execute_query(
            'SELECT id FROM groups WHERE source = %s AND ldap_dn = %s',
            (gr.GROUP_SOURCE_AD, dn), fetch_one=True)
        if existing:
            return _error('GroupAlreadyAdded')
        if gr.group_name_exists(name):
            return _error('GroupNameExists')
        gid = gr.create_group(name, source=gr.GROUP_SOURCE_AD, ldap_dn=dn, ldap_sid=sid)
        add_audit_log(user['id'], 'ADD_AD_GROUP', name, f'AD group added (dn={dn})', request.remote_addr)
        return jsonify({'message': 'AD group added', 'id': gid}), 201

    @app.route('/api/web/ad/groups', methods=['GET'])
    def web_ad_group_search():
        """Поиск групп в каталоге AD для добавления в систему"""
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        from modules import ldap_auth
        if not ldap_auth.is_enabled():
            return _error('LDAPNotConfigured')
        found = ldap_auth.search_groups(request.args.get('search', ''))
        if found is None:
            return _error('LDAPError', 500)
        # Исключаем уже добавленные в систему группы
        added = {g['ldap_dn'] for g in gr.list_groups() if g.get('ldap_dn')}
        return jsonify([g for g in found if g['dn'] not in added])

    @app.route('/api/web/users/<int:user_id>/group', methods=['PUT'])
    def web_user_set_group(user_id):
        """Назначить пользователю первичную группу"""
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        target = execute_query('SELECT id FROM users WHERE id = %s', (user_id,), fetch_one=True)
        if not target:
            return _error('ItemNotFound', 404)
        body = _parse_json_body() or {}
        group_id = body.get('group_id')
        if not group_id or not gr.group_info_by_id(group_id):
            return _error('ParamsError')
        ab.set_user_group(user_id, group_id)
        return jsonify({'message': 'User group updated'})

    # ========== ВЕБ-UI: АУДИТ БЕЗОПАСНОСТИ ==========
    def _audit_page():
        page = max(1, request.args.get('page', 1, type=int))
        psz = min(200, max(1, request.args.get('page_size', 20, type=int)))
        return page, psz, (page - 1) * psz

    @app.route('/api/web/audit/conn', methods=['GET'])
    def web_audit_conn():
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        page, psz, off = _audit_page()
        total = execute_query(
            "SELECT COUNT(*) AS c FROM rustdesk_audits WHERE audit_type='conn'", fetch_one=True)
        rows = execute_query("""
            SELECT id, device_id, uuid, conn_id, from_peer, from_name, conn_type,
                   created_at, close_time
            FROM rustdesk_audits WHERE audit_type='conn'
            ORDER BY created_at DESC LIMIT %s OFFSET %s
        """, (psz, off), fetch_all=True)
        return jsonify({'total': (total or {}).get('c', 0), 'rows': rows or []})

    @app.route('/api/web/audit/file', methods=['GET'])
    def web_audit_file():
        user = get_auth_user()
        if not user or not gr.is_admin_user(user):
            return _error('NoAccess', 403)
        page, psz, off = _audit_page()
        total = execute_query(
            "SELECT COUNT(*) AS c FROM rustdesk_audits WHERE audit_type='file'", fetch_one=True)
        rows = execute_query("""
            SELECT id, device_id, uuid, conn_id, file_type, path, is_file, info, created_at
            FROM rustdesk_audits WHERE audit_type='file'
            ORDER BY created_at DESC LIMIT %s OFFSET %s
        """, (psz, off), fetch_all=True)
        return jsonify({'total': (total or {}).get('c', 0), 'rows': rows or []})
