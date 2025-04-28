#!/bin/bash
set -e

RAMDISK_PATH="/mnt/ramdisk/minecraft_world"
RAID_PATH="/mnt/raid/minecraft/world"

echo "[1/2] Копирование мира из RAM обратно на RAID..."
rsync -a --delete "$RAMDISK_PATH/" "$RAID_PATH/"

echo "[2/2] Размонтирование tmpfs..."
sudo umount "$RAMDISK_PATH"
