#!/bin/bash
# quick-commit-fast.sh - Быстрый коммит без запросов

if [ -z "$1" ]; then
    echo "❌ Ошибка: не указано сообщение коммита"
    echo "Использование: ./quick-commit-fast.sh \"Сообщение\""
    exit 1
fi

git add .
git commit -m "$1"
git push