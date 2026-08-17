import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import zipfile

from modules.database import execute_query

DATA_DIR = os.environ.get('CLIENTGEN_DIR', '/data/clientgen')
RDGEN_CLI = os.environ.get('RDGEN_CLI', '')          # путь к rdgen_cli.py или исполняемому файлу
RDGEN_SERVER = os.environ.get('RDGEN_SERVER', '')    # адрес RDGen сервера

# правила валидации совпадают с формой rdgen (PLATFORM/VERSION/... choices)
PLATFORM_CHOICES = ('windows', 'windows-x86', 'linux', 'android', 'macos')
VERSION_CHOICES = ('master', '1.4.9', '1.4.8', '1.4.7', '1.4.6', '1.4.5',
                   '1.4.4', '1.4.3', '1.4.2', '1.4.1', '1.4.0')
DIRECTION_CHOICES = ('incoming', 'outgoing', 'both')
INSTALLATION_CHOICES = ('installationY', 'installationN')
SETTINGS_CHOICES = ('settingsY', 'settingsN')

DEFAULT_CONFIG = {
    "platform": "windows", "version": "1.4.9",
    "appname": "", "direction": "both",
    "installation": "installationY", "settings": "settingsY",
    "serverIP": "", "key": "", "apiServer": "",
    "iconbase64": "", "logobase64": "",
    "permanentPassword": "", "defaultManual": "", "overrideManual": "", "note": "",
}

# обязательные ChoiceFields веб-формы rdgen (POST /generator), которых нет в
# нашей схеме; rdgen-cli шлёт конфиг как form-data, без них форма invalid
RDGEN_FORM_DEFAULTS = {
    "theme": "system", "themeDorO": "default",
    "passApproveMode": "password-click",
    "permissionsDorO": "default", "permissionsType": "custom",
}


def _png_dims(data_url):
    if not isinstance(data_url, str) or ';base64,' not in data_url:
        return None
    header, encoded = data_url.split(';base64,', 1)
    if 'image/png' not in header:
        return None
    try:
        data = base64.b64decode(encoded)
    except Exception:
        return None
    if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return struct.unpack('>II', data[16:24])


def validate_config(name, cfg):
    errors = {}
    if not (name or '').strip():
        errors['name'] = 'This field is required.'
    elif not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        errors['name'] = 'English letters, digits, "-" and "_" only (no spaces).'
    if not isinstance(cfg, dict):
        return {'config_json': 'Must be an object.'}
    appname = (cfg.get('appname') or '').strip()
    if not appname:
        errors['appname'] = 'This field is required.'
    elif not appname.isascii():
        errors['appname'] = 'English characters only.'
    for field, choices in (('platform', PLATFORM_CHOICES), ('version', VERSION_CHOICES),
                           ('direction', DIRECTION_CHOICES), ('installation', INSTALLATION_CHOICES),
                           ('settings', SETTINGS_CHOICES)):
        if cfg.get(field) not in choices:
            errors[field] = 'Invalid choice. Must be one of: %s' % list(choices)
    for field in ('serverIP', 'key', 'apiServer', 'permanentPassword',
                  'defaultManual', 'overrideManual', 'note'):
        if cfg.get(field) is not None and not isinstance(cfg.get(field), str):
            errors[field] = 'Must be a string.'
    if cfg.get('iconbase64'):
        dims = _png_dims(cfg['iconbase64'])
        if dims is None:
            errors['iconbase64'] = 'Only PNG images are allowed.'
        elif dims[0] != dims[1]:
            errors['iconbase64'] = 'Icon dimensions must be square.'
    if cfg.get('logobase64') and _png_dims(cfg['logobase64']) is None:
        errors['logobase64'] = 'Only PNG images are allowed.'
    return errors


def _row(r):
    if not r:
        return None
    return dict(r)


def list_configs():
    return execute_query(
        "SELECT * FROM client_configs ORDER BY id", fetch_all=True) or []


def get_config(cid):
    return _row(execute_query(
        "SELECT * FROM client_configs WHERE id=%s", (cid,), fetch_one=True))


def create_config(name, platform, version, author, config_json):
    return execute_query("""
        INSERT INTO client_configs (name, platform, version, author, config_json)
        VALUES (%s,%s,%s,%s,%s) RETURNING id
    """, (name, platform, version, author, config_json), fetch_one=True)['id']


def update_config(cid, name, platform, version, config_json):
    # при изменении конфигурации сбрасываем сборку и удаляем артефакт
    cfg = get_config(cid)
    if cfg and cfg['artifact_dir'] and os.path.isdir(cfg['artifact_dir']):
        shutil.rmtree(cfg['artifact_dir'], ignore_errors=True)
    execute_query("""
        UPDATE client_configs SET name=%s, platform=%s, version=%s, config_json=%s,
            build_status='none', build_log='', build_date=NULL, artifact_dir='', updated_at=NOW()
        WHERE id=%s
    """, (name, platform, version, config_json, cid))


def delete_config(cid):
    cfg = get_config(cid)
    if cfg and cfg['artifact_dir'] and os.path.isdir(cfg['artifact_dir']):
        shutil.rmtree(cfg['artifact_dir'], ignore_errors=True)
    execute_query("DELETE FROM client_configs WHERE id=%s", (cid,))


def duplicate_config(cid, author):
    cfg = get_config(cid)
    if not cfg:
        return None
    return create_config(cfg['name'] + ' (copy)', cfg['platform'], cfg['version'],
                         author, cfg['config_json'])


def _artifact_dir(cid):
    d = os.path.join(DATA_DIR, str(cid))
    os.makedirs(d, exist_ok=True)
    return d


def start_build(cid):
    cfg = get_config(cid)
    if not cfg:
        return False, 'not found'
    if cfg['build_status'] == 'running':
        return False, 'already running'
    if not RDGEN_CLI or not RDGEN_SERVER:
        return False, 'rdgen-cli не настроен (RDGEN_CLI / RDGEN_SERVER)'
    execute_query("UPDATE client_configs SET build_status='running', build_log='', updated_at=NOW() WHERE id=%s", (cid,))
    t = threading.Thread(target=_run_build, args=(cid,), daemon=True)
    t.start()
    return True, 'started'


def _run_build(cid):
    cfg = get_config(cid)
    art = _artifact_dir(cid)
    cfg_path = os.path.join(art, 'config.json')
    try:
        # rdgen требует exename; для старых записей без него — имя конфигурации
        data = {**RDGEN_FORM_DEFAULTS, **json.loads(cfg['config_json'])}
        data['exename'] = data.get('exename') or cfg['name']
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        exe = [sys.executable] if RDGEN_CLI.endswith('.py') else []
        cmd = exe + [RDGEN_CLI, '-f', cfg_path, '-s', RDGEN_SERVER, '-d']
        proc = subprocess.run(cmd, cwd=art, capture_output=True, text=True, timeout=3600)
        log = (proc.stdout or '') + (proc.stderr or '')
        if proc.returncode == 0:
            execute_query("""UPDATE client_configs SET build_status='success', build_log=%s,
                             build_date=NOW(), artifact_dir=%s, updated_at=NOW() WHERE id=%s""",
                          (log, art, cid))
        else:
            execute_query("""UPDATE client_configs SET build_status='failed', build_log=%s, updated_at=NOW() WHERE id=%s""",
                          (log, cid))
    except Exception as e:
        execute_query("""UPDATE client_configs SET build_status='failed', build_log=%s, updated_at=NOW() WHERE id=%s""",
                      (str(e), cid))


def artifact_zip(cid):
    """Создаёт zip из артефактов и возвращает путь (или None)."""
    cfg = get_config(cid)
    if not cfg or cfg['build_status'] != 'success':
        return None
    art = cfg['artifact_dir']
    if not art or not os.path.isdir(art):
        return None
    files = [f for f in os.listdir(art) if os.path.isfile(os.path.join(art, f)) and f != 'config.json']
    if not files:
        return None
    if len(files) == 1:
        return os.path.join(art, files[0])
    zpath = os.path.join(art, 'build.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(art, f), f)
    return zpath
