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

USERNAME = 'admin'
PASSWORD = 'password123'

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
        ramdisk_path = os.path.join(RAMDISK_PATH, f"{server}_world")
        active = os.path.isdir(ramdisk_path) and bool(os.listdir(ramdisk_path))
        busy = is_server_busy(server)
        servers.append({'name': server, 'active': active, 'busy': busy})
    return servers

def is_server_busy(server_name):
    proc = server_processes.get(server_name)
    if proc and proc.poll() is None:
        return True
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
    ok, msg = run_server_script(server_name, script_file)
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

def run_server_script(server_name, script_name):
    script_path = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", script_name)
    if not os.path.isfile(script_path) or not os.access(script_path, os.X_OK):
        return False, "Файл не найден или не исполняемый."
    if is_server_busy(server_name):
        return False, "Скрипт уже выполняется."
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    action = script_name.replace('.sh', '')
    log_file = os.path.join(LOGS_DIR, f"{server_name}_{action}_{ts}.log")
    try:
        with open(log_file, "w") as f:
            result = subprocess.run([script_path], stdout=f, stderr=subprocess.STDOUT, check=False)
        return True, f"Скрипт выполнен (код {result.returncode}). Лог сохранён."
    except Exception as e:
        return False, f"Ошибка запуска: {e}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8390, debug=True)