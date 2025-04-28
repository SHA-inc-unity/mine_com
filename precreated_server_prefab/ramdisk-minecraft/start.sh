#!/bin/bash
set -e

# Определяем имя сервера из папки, где лежит start.sh
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SERVER_NAME="$(basename "$(dirname "$SCRIPT_DIR")")"

SERVER_PROPERTIES="${SCRIPT_DIR}/server.properties"
PORT1=$(grep -E "^server-port=" "$SERVER_PROPERTIES" | cut -d'=' -f2 | tr -d '\r')
PORT2=$(grep -E "^rcon.port=" "$SERVER_PROPERTIES" | cut -d'=' -f2 | tr -d '\r')
PORT1=${PORT1:-25565}
PORT2=${PORT2:-25575}

# Проверка структуры папок
if [ ! -d "${SCRIPT_DIR}/../neoforge-server" ]; then
  echo "❌ Ошибка структуры! Ожидается:"
  echo "├── ${SERVER_NAME}/"
  echo "│   ├── neoforge-server/    # Серверные файлы"
  echo "│   └── ramdisk-minecraft/  # Текущая папка со скриптом"
  exit 1
fi

# Пути с учётом структуры
RAMDISK_PATH="/mnt/ramdisk/${SERVER_NAME}_world"
RAID_WORLD_PATH="/mnt/raid/minecraft/${SERVER_NAME}/world"
BLUEMAP_PATH="/mnt/raid/minecraft/${SERVER_NAME}/bluemap"
MODS_DIR="${SCRIPT_DIR}/../neoforge-server/mods"
MODS_LIST="${SCRIPT_DIR}/.last_mods.txt"
TMPFS_SIZE_BUFFER_MB=16384

# Нормализация пути к neoforge-server
NEOFORGE_SERVER_PATH="$(realpath "${SCRIPT_DIR}/../neoforge-server")"

echo "������ Запуск процесса для сервера: ${SERVER_NAME}"
echo "[1/5] Подготовка файловой структуры..."
sudo mkdir -p "$RAID_WORLD_PATH" "$BLUEMAP_PATH"

echo "[2/5] Проверка размера мира..."
USED_MB=$(du -sm "$RAID_WORLD_PATH" | awk '{print $1}' || echo "0")
TOTAL_MB=$((USED_MB + TMPFS_SIZE_BUFFER_MB))

echo "[3/5] Настройка RAM-диска (${TOTAL_MB}MB)..."
sudo mkdir -p "$RAMDISK_PATH"
sudo mount -t tmpfs -o size=${TOTAL_MB}M tmpfs "$RAMDISK_PATH" || sudo mount -o remount,size=${TOTAL_MB}M "$RAMDISK_PATH"

echo "[4/5] Синхронизация мира..."
rsync -a --delete "$RAID_WORLD_PATH/" "$RAMDISK_PATH/"

echo "[5/5] Генерация docker-compose.yml..."

cat > "${SCRIPT_DIR}/docker-compose.yml" <<EOF
version: '3.8'

services:
  minecraft:
    build: 
      context: ${NEOFORGE_SERVER_PATH}
      args:
          SERVER_NAME: ${SERVER_NAME}
    container_name: ${SERVER_NAME}-server
    ports:
      - "${PORT1}:${PORT1}"
      - "8123:8123"
      - "${PORT2}:${PORT2}"
      - "7979:7979"
    volumes:
      - ${RAMDISK_PATH}:/server/${SERVER_NAME}/world
      - ${BLUEMAP_PATH}:/server/${SERVER_NAME}/bluemap
      - ${SCRIPT_DIR}/user_jvm_args.txt:/server/${SERVER_NAME}/user_jvm_args.txt:ro
      - ${SCRIPT_DIR}/server.properties:/server/${SERVER_NAME}/server.properties:ro
    restart: unless-stopped
EOF

# Управление модами
echo "������ Проверка модов..."
TEMP_MODS=$(mktemp)
find "$MODS_DIR" -type f -name "*.jar" -exec basename {} \; | sort > "$TEMP_MODS"

if [ -f "$MODS_LIST" ]; then
  if diff -q "$TEMP_MODS" "$MODS_LIST" >/dev/null; then
    echo "✅ Моды не изменялись"
    docker-compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d
  else
    echo "������ Обнаружены изменения модов"
    cp "$TEMP_MODS" "$MODS_LIST"
    docker-compose -f "${SCRIPT_DIR}/docker-compose.yml" up --build -d
  fi
else
  echo "������ Первоначальная сборка"
  cp "$TEMP_MODS" "$MODS_LIST"
  docker-compose -f "${SCRIPT_DIR}/docker-compose.yml" up --build -d
fi

rm "$TEMP_MODS"
echo -e "\n✅ Сервер \e[1;32m${SERVER_NAME}\e[0m успешно запущен!"
echo "������ Мир сохранён в: $RAID_WORLD_PATH"
echo "������️ Bluemap: $BLUEMAP_PATH"