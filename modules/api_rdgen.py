"""Callback-эндпоинты для воркфлоу generator-*.yml (порт rdgen).

Воркфлоу GitHub Actions вызывают их напрямую по GENURL:
  GET  /get_zip             — скачивание AES-архива с секретами сборки
  GET  /get_png             — иконка/логотип сборки
  POST /save_custom_client  — выгрузка готового артефакта (multipart)
  POST /updategh            — статус сборки {uuid, status}
  POST /cleanzip            — удаление архива секретов {uuid}

Авторизация: Bearer-токен сборки (поле token в секретах) для
save_custom_client/updategh; остальные защищены неугадываемым uuid.
"""
import os

from flask import request, jsonify, send_file, abort

from modules import clientgen


def _check_build_token(uuid_val):
    """Bearer-токен запроса должен совпадать с токеном сборки."""
    cfg = clientgen.get_config_by_uuid(uuid_val) if uuid_val else None
    if not cfg or not cfg.get('build_token'):
        return False
    auth = request.headers.get('Authorization', '')
    return auth == 'Bearer ' + cfg['build_token']


def init_rdgen_routes(app):

    @app.route('/get_zip', methods=['GET'])
    def rdgen_get_zip():
        path = clientgen.temp_zip_path(request.args.get('filename', ''))
        if not path:
            abort(404)
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))

    @app.route('/get_png', methods=['GET'])
    def rdgen_get_png():
        uuid_val = request.args.get('uuid', '')
        filename = os.path.basename(request.args.get('filename', ''))
        if not uuid_val or filename not in ('icon.png', 'logo.png', 'privacy.png'):
            abort(404)
        path = os.path.join(clientgen.DATA_DIR, 'png', uuid_val, filename)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype='image/png')

    @app.route('/save_custom_client', methods=['POST'])
    def rdgen_save_client():
        uuid_val = request.form.get('uuid', '')
        if not _check_build_token(uuid_val):
            return jsonify({'error': 'Unauthorized'}), 401
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'error': 'No file'}), 400
        path = clientgen.save_artifact(uuid_val, f.filename, f)
        if not path:
            return jsonify({'error': 'Unknown uuid'}), 404
        return 'File saved successfully!'

    @app.route('/updategh', methods=['POST'])
    def rdgen_update_status():
        data = request.get_json(silent=True) or {}
        uuid_val = data.get('uuid', '')
        if not _check_build_token(uuid_val):
            return jsonify({'error': 'Unauthorized'}), 401
        clientgen.apply_callback_status(uuid_val, data.get('status', ''))
        return ''

    @app.route('/cleanzip', methods=['POST'])
    def rdgen_cleanzip():
        data = request.get_json(silent=True) or {}
        uuid_val = data.get('uuid', '')
        if not uuid_val:
            return jsonify({'error': 'Missing uuid'}), 400
        clientgen.cleanup_temp_zips(uuid_val)
        return 'Cleanup successful'
