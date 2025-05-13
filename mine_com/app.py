import os
import shutil
import psutil
import subprocess
import datetime
import glob
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = 'supersecretkey123'

MINECRAFT_SERVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RAMDISK_PATH = '/mnt/ramdisk'
LOGS_DIR = os.path.join(MINECRAFT_SERVERS_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

server_processes = {}
busy_pids = {}  # <--- здесь храним pid активных start/stop процессов

USERNAME = 'admin'
PASSWORD = 'password123'

def is_pid_running(pid):
    """Проверяет, существует ли процесс с таким pid."""
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False

def get_system_resources():
    disk_root = shutil.disk_usage('/')
    disk_root_total = disk_root.total // (1024 ** 3)
    disk_root_used = disk_root.used // (1024 ** 3)
    disk_root_free = disk_root.free // (1024 ** 3)

    try:
        disk_raid = shutil.disk_usage('/mnt/raid')
        disk_raid_total = disk_raid.total // (1024 ** 3)
        disk_raid_used = disk_raid.used // (1024 ** 3)
        disk_raid_free = disk_raid.free // (1024 ** 3)
    except FileNotFoundError:
        disk_raid_total = disk_raid_used = disk_raid_free = "N/A"

    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    memory_total = memory.total // (1024 ** 3)
    memory_used = memory.used // (1024 ** 3)
    memory_free = memory.free // (1024 ** 3)

    return {
        'disk_root': {'total': disk_root_total, 'used': disk_root_used, 'free': disk_root_free},
        'disk_raid': {'total': disk_raid_total, 'used': disk_raid_used, 'free': disk_raid_free},
        'cpu_usage': cpu_usage,
        'memory': {'total': memory_total, 'used': memory_used, 'free': memory_free}
    }

def get_servers_with_status():
    servers = []
    all_servers = [d for d in os.listdir(MINECRAFT_SERVERS_DIR)
                  if os.path.isdir(os.path.join(MINECRAFT_SERVERS_DIR, d))
                  and d not in ("mine_com", "logs", ".git", "precreated_server_prefab")]
    for server in all_servers:
        active = is_server_busy(server)
        pid = busy_pids.get(server)
        busy = is_pid_running(pid)
        servers.append({'name': server, 'active': active, 'busy': busy})
    return servers

def is_server_busy(server_name):
    """
    Проверяет, запущен ли контейнер сервера с именем {server_name}-server.
    Возвращает True если контейнер работает, иначе False.
    """
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--filter", f"name=^{server_name}-server$", "--format", "{{.ID}}"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return bool(output)
    except Exception:
        return False

@app.route('/')
def list_servers():
    if 'logged_in' not in session or not session['logged_in']:
        return redirect(url_for('login'))
    servers = get_servers_with_status()
    resources = get_system_resources()
    return render_template('index.html', servers=servers, resources=resources)

@app.route('/resources')
def resources():
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(get_system_resources())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == USERNAME and password == PASSWORD:
            session['logged_in'] = True
            flash('Успешный вход!', 'success')
            return redirect(url_for('list_servers'))
        else:
            flash('Неверный логин или пароль', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Вы вышли из системы', 'success')
    return redirect(url_for('login'))

@app.route('/server_status')
def server_status():
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': 'Unauthorized'}), 401
    servers = get_servers_with_status()
    return jsonify({'servers': servers})

@app.route('/server/<server_name>/<action>', methods=['POST'])
def server_action(server_name, action):
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    if action not in ['start', 'stop']:
        return jsonify({'success': False, 'error': 'Unknown action'}), 400
    script_file = 'start.sh' if action == 'start' else 'stop.sh'
    if action == 'start':
        patch_bluemap_configs(server_name)
    ok, msg, pid = run_server_script(server_name, script_file)
    if ok and pid:
        busy_pids[server_name] = pid
    return jsonify({'success': ok, 'message': msg})

@app.route('/server/<server_name>/<action>/log')
def server_action_log(server_name, action):
    if action not in ("start", "stop"):
        return jsonify({"log": "Неверное действие"}), 400
    log_mask = os.path.join(LOGS_DIR, f"{server_name}_{action}_*.log")
    log_files = sorted(glob.glob(log_mask), reverse=True)
    if not log_files:
        return jsonify({"log": "Нет лога"}), 404
    log_file = log_files[0]
    with open(log_file, "rb") as f:
        log_content = f.read().decode("utf-8", errors="replace")
    return jsonify({"log": log_content})

@app.route('/server/<server_name>/properties', methods=['GET'])
def get_properties(server_name):
    prop_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "server.properties")
    if not os.path.isfile(prop_path):
        return jsonify({"error": "Файл не найден"}), 404
    with open(prop_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return jsonify({"text": text})

@app.route('/server/<server_name>/properties', methods=['POST'])
def save_properties(server_name):
    data = request.get_json()
    text = data.get("text", "")
    prop_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "server.properties")
    try:
        with open(prop_path, "w", encoding="utf-8") as f:
            f.write(text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/server/<server_name>/jvmargs', methods=['GET'])
def get_jvmargs(server_name):
    jvm_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "user_jvm_args.txt")
    if not os.path.isfile(jvm_path):
        return jsonify({"error": "Файл не найден"}), 404
    with open(jvm_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return jsonify({"text": text})

@app.route('/server/<server_name>/jvmargs', methods=['POST'])
def save_jvmargs(server_name):
    data = request.get_json()
    text = data.get("text", "")
    jvm_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "user_jvm_args.txt")
    try:
        with open(jvm_path, "w", encoding="utf-8") as f:
            f.write(text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/server/<server_name>/metrics')
def server_metrics(server_name):
    ramdisk_path = os.path.join(RAMDISK_PATH, f"{server_name}_world")
    raid_world_path = os.path.join('/mnt/raid/minecraft', server_name, 'world')

    # CPU/RAM через docker stats
    cpu_percent = 0
    mem_percent = 0
    mem_used = 0
    mem_total = 0
    try:
        cname = f"{server_name}-server"
        output = subprocess.check_output([
            "docker", "stats", "--no-stream", "--format",
            "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"
        ]).decode().splitlines()
        for line in output:
            if line.startswith(cname + " "):
                parts = line.split()
                cpu_percent = float(parts[1].replace('%', '').replace(',', '.'))
                mem_used = parts[2]
                mem_total = parts[4]
                def parse_mem(m):
                    m = m.replace(",", ".")
                    if m.endswith("GiB"):
                        return float(m[:-3])
                    elif m.endswith("MiB"):
                        return float(m[:-3]) / 1024
                    elif m.endswith("KiB"):
                        return float(m[:-3]) / (1024*1024)
                    else:
                        return float(m)
                mem_used_val = parse_mem(mem_used)
                mem_total_val = parse_mem(mem_total)
                mem_percent = int(round((mem_used_val / mem_total_val)*100)) if mem_total_val else 0
                mem_used = round(mem_used_val, 2)
                mem_total = round(mem_total_val, 2)
                break
    except Exception as ex:
        cpu_percent = 0
        mem_percent = 0
        mem_used = 0
        mem_total = 0

    # Размер папки мира на RAMDISK (в байтах)
    ramdisk_size_bytes = 0
    if os.path.isdir(ramdisk_path):
        try:
            du_out = subprocess.check_output(['du', '-sb', ramdisk_path]).decode().split()[0]
            ramdisk_size_bytes = int(du_out)
        except Exception as ex:
            ramdisk_size_bytes = 0

    # Размер папки мира на RAID (в байтах)
    raid_size_bytes = 0
    if os.path.isdir(raid_world_path):
        try:
            du_out = subprocess.check_output(['du', '-sb', raid_world_path]).decode().split()[0]
            raid_size_bytes = int(du_out)
        except Exception as ex:
            raid_size_bytes = 0

    # RAMDISK: процент использования относительно размера ТОМа RAMDISK для этого mountpoint
    ramdisk_percent = None
    try:
        if os.path.isdir(ramdisk_path):
            ramdisk_total = shutil.disk_usage(ramdisk_path).total
            if ramdisk_total > 0:
                ramdisk_percent = round(ramdisk_size_bytes / ramdisk_total * 100, 2)
            else:
                ramdisk_percent = 0
        else:
            ramdisk_percent = None
    except Exception:
        ramdisk_percent = None

    # Root: процент относительно занятого места на /
    try:
        disk_root = shutil.disk_usage('/')
        root_used = disk_root.used
        if root_used > 0:
            root_usage_percent = round(ramdisk_size_bytes / root_used * 100, 2)
        else:
            root_usage_percent = 0
    except Exception:
        root_usage_percent = 0

    # RAID: процент относительно занятого места на RAID
    try:
        disk_raid = shutil.disk_usage('/mnt/raid')
        raid_used = disk_raid.used
        if raid_used > 0:
            raid_usage_percent = round(raid_size_bytes / raid_used * 100, 2)
        else:
            raid_usage_percent = 0
    except Exception:
        raid_usage_percent = 0

    print(f"[{server_name}] ramdisk_path={ramdisk_path} exists={os.path.isdir(ramdisk_path)} size={ramdisk_size_bytes}")
    print(f"[{server_name}] raid_world_path={raid_world_path} exists={os.path.isdir(raid_world_path)} size={raid_size_bytes}")
    print(f"[{server_name}] ramdisk_percent={ramdisk_percent} root_usage_percent={root_usage_percent} raid_usage_percent={raid_usage_percent}")

    return jsonify({
        "cpu": int(round(cpu_percent)),
        "memory": {
            "percent": int(round(mem_percent)),
            "used": mem_used,
            "total": mem_total
        },
        "root_usage_percent": root_usage_percent,
        "raid_usage_percent": raid_usage_percent,
        "ramdisk_percent": ramdisk_percent
    })


@app.route('/get_version')
def get_version():
    try:
        log = subprocess.check_output(
            ["git", "log", "--pretty=format:%s"],
            encoding="utf-8"
        ).splitlines()

        last_global_index = None
        for i, msg in enumerate(log):
            if "global" in msg.lower():
                last_global_index = i
                break

        if last_global_index is not None:
            major = sum(1 for m in log if "global" in m.lower() and log.index(m) <= last_global_index)
            after_global = log[:last_global_index]
            minor = sum(1 for m in after_global if "big" in m.lower())
            last_big_index = None
            for i, msg in enumerate(after_global):
                if "big" in msg.lower():
                    last_big_index = i
                    break
            if last_big_index is not None:
                patch = last_big_index
            else:
                patch = len(after_global)
        else:
            last_big_index = None
            for i, msg in enumerate(log):
                if "big" in msg.lower():
                    last_big_index = i
                    break
            if last_big_index is not None:
                major = 0
                minor = sum(1 for m in log if "big" in m.lower() and log.index(m) <= last_big_index)
                patch = last_big_index
            else:
                major = 0
                minor = 0
                patch = len(log)

        version = f"{major}.{minor}.{patch}"
        return jsonify({"version": version})

    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

@app.route('/create_server', methods=['POST'])
def create_server():
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json()
    server_name = data.get('server_name', '').strip()

    if not server_name or not server_name.isidentifier():
        return jsonify({'success': False, 'error': 'Некорректное имя сервера'}), 400

    src = os.path.join(MINECRAFT_SERVERS_DIR, 'precreated_server_prefab')
    dst = os.path.join(MINECRAFT_SERVERS_DIR, server_name)

    if not os.path.isdir(src):
        return jsonify({'success': False, 'error': 'Шаблон не найден'}), 500
    if os.path.exists(dst):
        return jsonify({'success': False, 'error': 'Сервер с таким именем уже существует'}), 400

    try:
        shutil.copytree(src, dst)
        return jsonify({'success': True, 'message': f'Сервер {server_name} создан!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/server/<server_name>/config/list', methods=['GET'])
def list_config_files(server_name):
    import os
    rel_path = request.args.get('path', '')
    config_root = os.path.join(MINECRAFT_SERVERS_DIR, server_name, 'neoforge-server', 'config')
    abs_path = os.path.normpath(os.path.join(config_root, rel_path))
    if not abs_path.startswith(config_root):
        return jsonify({'error': 'Недопустимый путь'}), 400
    if not os.path.isdir(abs_path):
        return jsonify({'error': 'Папка не найдена'}), 404
    items = []
    for name in sorted(os.listdir(abs_path)):
        full = os.path.join(abs_path, name)
        if os.path.isdir(full):
            items.append({'name': name, 'type': 'dir'})
        else:
            items.append({'name': name, 'type': 'file'})
    parent = None
    if abs_path != config_root:
        parent = os.path.relpath(os.path.dirname(abs_path), config_root)
        if parent == '.':
            parent = ''
    return jsonify({'items': items, 'parent': parent, 'current': os.path.relpath(abs_path, config_root)})

@app.route('/server/<server_name>/config/file', methods=['GET', 'POST'])
def config_file(server_name):
    import os
    rel_path = request.args.get('path', '')
    config_root = os.path.join(MINECRAFT_SERVERS_DIR, server_name, 'neoforge-server', 'config')
    abs_path = os.path.normpath(os.path.join(config_root, rel_path))
    if not abs_path.startswith(config_root):
        return jsonify({'error': 'Недопустимый путь'}), 400
    if request.method == 'GET':
        if not os.path.isfile(abs_path):
            return jsonify({'error': 'Файл не найден'}), 404
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        return jsonify({'text': text, 'filename': os.path.basename(abs_path)})
    else:
        data = request.get_json()
        text = data.get('text', '')
        try:
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(text)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

def update_bluemap_config(server_name):
    config_dir = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "neoforge-server", "config", "bluemap")
    replacements = {
        "core.conf": [
            ('data: "/server/', 'data: "/server/{server_name}/bluemap/')
        ],
        "webapp.conf": [
            ('webroot: "/server/', 'webroot: "/server/{server_name}/bluemap/web')
        ],
        "webserver.conf": [
            ('webroot: "/server/', 'webroot: "/server/{server_name}/bluemap/web')
        ]
    }

    for filename, patterns in replacements.items():
        filepath = os.path.join(config_dir, filename)
        if not os.path.isfile(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            for oldpat, fullpat in patterns:
                import re
                content = re.sub(
                    rf'{oldpat}[^/]+/bluemap(/web)?',
                    f'{oldpat}{server_name}/bluemap\\1',
                    content
                )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as ex:
            print(f"Не удалось обновить {filepath}: {ex}")

def run_server_script(server_name, script_name):
    script_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", script_name)
    if not os.path.isfile(script_path) or not os.access(script_path, os.X_OK):
        return False, "Файл не найден или не исполняемый.", None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    action = script_name.replace('.sh', '')
    log_file = os.path.join(LOGS_DIR, f"{server_name}_{action}_{ts}.log")
    try:
        with open(log_file, "w") as f:
            proc = subprocess.Popen([script_path], stdout=f, stderr=subprocess.STDOUT)
        return True, f"Скрипт запущен (pid {proc.pid}). Лог пишется.", proc.pid
    except Exception as e:
        return False, f"Ошибка запуска: {e}", None

def patch_bluemap_configs(server_name):
    import os

    config_dir = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "neoforge-server", "config", "bluemap"
    )
    patch_list = [
        {
            "filename": "core.conf",
            "key": "data:",
            "value": f'data: "/server/{server_name}/bluemap/"'
        },
        {
            "filename": "webapp.conf",
            "key": "webroot:",
            "value": f'webroot: "/server/{server_name}/bluemap/web"'
        },
        {
            "filename": "webserver.conf",
            "key": "webroot:",
            "value": f'webroot: "/server/{server_name}/bluemap/web"'
        },
        {
            "filename": os.path.join("storages", "file.conf"),
            "key": "root:",
            "value": f'root: "/server/{server_name}/bluemap/web/maps"'
        },
    ]

    for patch in patch_list:
        path = os.path.join(config_dir, patch["filename"])
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(path, "w", encoding="utf-8") as f:
                for line in lines:
                    if line.strip().startswith(patch["key"]):
                        f.write(patch["value"] + "\n")
                    else:
                        f.write(line)
        except Exception as ex:
            print(f"Ошибка обновления {path}: {ex}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8390, debug=True)