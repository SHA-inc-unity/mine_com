#!/bin/bash

# 1. Активация venv
source ./venv/bin/activate

# 2. Запуск Flask-приложения через nohup (порт 8390, как в app.py)
nohup python3 app.py > server.log 2>&1 &

# 3. Выход из venv
deactivate

echo "Скрипт запущен, логи пишутся в server.log"