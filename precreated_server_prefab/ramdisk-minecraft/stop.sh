#!/bin/bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SERVER_NAME="$(basename "$(dirname "$SCRIPT_DIR")")"
RAMDISK_PATH="/mnt/ramdisk/${SERVER_NAME}_world"
RAID_PATH="/mnt/raid/minecraft/${SERVER_NAME}/world"

echo "�� Остановка сервера: ${SERVER_NAME}"

# Проверка и выполнение пункта 1 только если установлен Docker
if command -v docker &> /dev/null; then
    echo "[1/3] Остановка Docker-контейнера..."
    docker-compose -f "${SCRIPT_DIR}/docker-compose.yml" down
else
    echo "[1/3] Пропуск: Docker не установлен"
fi

# Проверка и выполнение пункта 2 только если существует RAMDISK_PATH
if [ -d "$RAMDISK_PATH" ]; then
    echo "[2/3] Сохранение мира из RAM на RAID..."
    
    # Проверка содержимого RAM-диска
    if [ -z "$(ls -A "$RAMDISK_PATH")" ]; then
        echo "⚠️ RAM-диск пуст. Пропуск копирования."
    else
        # Проверка и создание целевой папки
        if ! sudo mkdir -p "$RAID_PATH" 2>/dev/null; then
            echo "❌ Ошибка: Невозможно создать целевую директорию $RAID_PATH"
            exit 1
        fi

        # Проверка прав на запись
        if [ ! -w "$RAID_PATH" ]; then
            echo "❌ Ошибка: Нет прав на запись в $RAID_PATH"
            exit 1
        fi

        # Копирование с проверкой результата
        echo "�� Синхронизация данных из $RAMDISK_PATH в $RAID_PATH"
        if sudo rsync -a --delete --checksum "${RAMDISK_PATH}/" "${RAID_PATH}/"; then
            echo "✅ Синхронизация успешно завершена"
            # Фикс прав
            sudo chown -R "$(id -u):$(id -g)" "$RAID_PATH"
            # Проверка целостности
            if diff -r "$RAMDISK_PATH" "$RAID_PATH" &>/dev/null; then
                echo "�� Проверка целостности: OK"
            else
                echo "❌ Ошибка: Расхождение в данных после копирования!"
                exit 1
            fi
        else
            echo "❌ Ошибка при синхронизации данных!"
            exit 1
        fi
    fi
else
    echo "[2/3] Пропуск: RAM-диск не найден (${RAMDISK_PATH})"
fi

# Пункт 3 всегда пытается размонтировать, если смонтирован
echo "[3/3] Проверка и размонтирование RAM-диска..."
if mountpoint -q "$RAMDISK_PATH"; then
    sudo umount "$RAMDISK_PATH"
    echo "✅ RAM-диск успешно размонтирован"
else
    echo "ℹ️ RAM-диск не был смонтирован, ничего не делаем"
fi

echo -e "\n✅ Сервер \e[1;32m${SERVER_NAME}\e[0m полностью остановлен"
echo "�� Мир сохранён в: ${RAID_PATH}"