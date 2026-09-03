import json
import os

from flask import request, jsonify, send_file

from modules import clientgen
from modules import groups as gr
from modules.api_ab import get_auth_user, _error


def _admin():
    user = get_auth_user()
    if not user:
        return None, (_error('Unauthorized', 401))
    if not gr.is_admin_user(user):
        return None, (_error('NoAccess', 403))
    return user, None


def init_clientgen_routes(app):

    @app.route('/api/web/clientgen/configs', methods=['GET'])
    def cg_list():
        user, err = _admin()
        if err:
            return err
        clientgen.refresh_running_configs()
        return jsonify(clientgen.list_configs())

    @app.route('/api/web/clientgen/configs', methods=['POST'])
    def cg_create():
        user, err = _admin()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        if not name:
            return _error('ParamsError')
        config_json = body.get('config_json')
        if config_json is None:
            return _error('ParamsError')
        cfg = json.loads(config_json) if isinstance(config_json, str) else config_json
        errors = clientgen.validate_config(name, cfg)
        if errors:
            return jsonify({'error': 'ValidationError', 'details': errors}), 400
        cfg['exename'] = name
        cid = clientgen.create_config(
            name, cfg.get('platform', 'windows'), cfg.get('version', ''),
            user.get('username', ''), json.dumps(cfg, ensure_ascii=False))
        return jsonify({'id': cid}), 201

    @app.route('/api/web/clientgen/configs/<int:cid>', methods=['GET'])
    def cg_get(cid):
        user, err = _admin()
        if err:
            return err
        cfg = clientgen.get_config(cid)
        if not cfg:
            return _error('ItemNotFound', 404)
        return jsonify(cfg)

    @app.route('/api/web/clientgen/configs/<int:cid>', methods=['PUT'])
    def cg_update(cid):
        user, err = _admin()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        if not name:
            return _error('ParamsError')
        config_json = body.get('config_json')
        if config_json is None:
            return _error('ParamsError')
        cfg = json.loads(config_json) if isinstance(config_json, str) else config_json
        errors = clientgen.validate_config(name, cfg)
        if errors:
            return jsonify({'error': 'ValidationError', 'details': errors}), 400
        cfg['exename'] = name
        clientgen.update_config(cid, name, cfg.get('platform', 'windows'),
                                cfg.get('version', ''), json.dumps(cfg, ensure_ascii=False))
        return jsonify({'message': 'updated'})

    @app.route('/api/web/clientgen/configs/<int:cid>', methods=['DELETE'])
    def cg_delete(cid):
        user, err = _admin()
        if err:
            return err
        clientgen.delete_config(cid)
        return jsonify({'message': 'deleted'})

    @app.route('/api/web/clientgen/configs/<int:cid>/duplicate', methods=['POST'])
    def cg_duplicate(cid):
        user, err = _admin()
        if err:
            return err
        nid = clientgen.duplicate_config(cid, user.get('username', ''))
        if not nid:
            return _error('ItemNotFound', 404)
        return jsonify({'id': nid}), 201

    @app.route('/api/web/clientgen/configs/<int:cid>/build', methods=['POST'])
    def cg_build(cid):
        user, err = _admin()
        if err:
            return err
        ok, msg = clientgen.start_build(cid)
        if not ok:
            return _error(msg)
        return jsonify({'message': 'started'})

    @app.route('/api/web/clientgen/configs/<int:cid>/status', methods=['GET'])
    def cg_status(cid):
        user, err = _admin()
        if err:
            return err
        cfg = clientgen.refresh_build_status(cid)
        if not cfg:
            return _error('ItemNotFound', 404)
        return jsonify({'build_status': cfg['build_status'],
                        'build_date': cfg['build_date'],
                        'build_log': cfg['build_log']})

    @app.route('/api/web/clientgen/configs/<int:cid>/download', methods=['GET'])
    def cg_download(cid):
        user, err = _admin()
        if err:
            return err
        path = clientgen.artifact_zip(cid)
        if not path:
            return _error('NoArtifact', 404)
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
