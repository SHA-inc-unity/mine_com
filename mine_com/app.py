import os
import shutil
import psutil
import subprocess
import datetime
import glob
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = 'supersecretkey123'

# Относительный путь к директории minecraft_servers
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
                  and d not in ("mine_com", "logs")]
    for server in all_servers:
        ramdisk_path = os.path.join(RAMDISK_PATH, f"{server}_world")
        # Проверяем, что папка существует и не пуста
        active = os.path.isdir(ramdisk_path) and bool(os.listdir(ramdisk_path))
        busy = is_server_busy(server)
        servers.append({'name': server, 'active': active, 'busy': busy})
    return servers

def is_server_busy(server_name):
    proc = server_processes.get(server_name)
    if proc and proc.poll() is None:
        return True
    return False

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8390, debug=True)