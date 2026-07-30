#!/bin/bash

echo "🚀 Подготовка к выгрузке на GitHub..."
echo "======================================"

# Проверяем наличие изменений
if [[ -z $(git status -s) ]]; then
    echo "❌ Нет изменений для коммита."
    exit 1
fi

echo "📝 Изменения для коммита:"
git status -s
echo ""

# Добавляем все изменения
echo "📦 Добавляем файлы..."
git add .

# Создаем коммит
echo "📝 Создаем коммит..."
git commit -m "feat: переход с SQLite на PostgreSQL и исправление timestamp

Основные изменения:

1. База данных:
   - Полный переход с SQLite на PostgreSQL
   - Добавлен сервис PostgreSQL в docker-compose.yml
   - Настроен пул соединений с БД (min=1, max=20)
   - Добавлены индексы для ускорения запросов

2. Миграция данных:
   - Создан скрипт migrate_data.py для переноса данных из SQLite
   - Поддержка миграции таблиц: computers, users, audit_log
   - Автоматическое преобразование timestamp

3. Исправление timestamp:
   - Пересчет last_online_timestamp из last_online для всех записей
   - Исправлены отрицательные значения diff_seconds
   - Корректное отображение времени последнего контакта

4. Зависимости:
   - Добавлен psycopg2-binary для работы с PostgreSQL
   - Обновлены версии в requirements.txt

5. Документация:
   - Обновлен README.md с описанием PostgreSQL
   - Добавлены инструкции по миграции

6. Исправление отображения:
   - Форматирование даты в admin.html
   - Корректный статус ONLINE/OFFLINE
   - Отображение diff_seconds

Файлы:
- modules/database.py (переписан под PostgreSQL)
- modules/__init__.py (обновлен)
- app.py (добавлены эндпоинты для диагностики БД)
- docker-compose.yml (добавлен сервис db)
- Dockerfile (установлен postgresql-client)
- requirements.txt (добавлен psycopg2-binary)
- migrate_data.py (скрипт миграции)
- fix_timestamps.py (скрипт исправления timestamp)
- static/admin.html (исправлен formatDate)
- README.md (обновлена документация)
"

# Проверяем наличие remote
if ! git remote | grep -q origin; then
    echo "➕ Добавляем remote origin..."
    git remote add origin https://github.com/fiverok/sveApiRust.git
fi

# Отправляем изменения
echo "⬆️ Отправляем на GitHub..."
git push -u origin main 2>/dev/null || git push -u origin master

# Создаем тег
echo "🏷️ Создаем тег v6.0.0"
git tag -a v6.0.0 -m "Версия 6.0.0: Переход на PostgreSQL

- Полный переход с SQLite на PostgreSQL
- Миграция данных из SQLite
- Исправление timestamp и статусов
- Улучшение производительности
- Оптимизация работы с БД через пул соединений"

git push origin v6.0.0

echo ""
echo "✅ Готово! Изменения выгружены на GitHub"
echo "🔗 Репозиторий: https://github.com/fiverok/sveApiRust"
echo "🏷️ Тег: v6.0.0"
echo ""
echo "📊 Статистика:"
echo "   - Всего коммитов: $(git rev-list --count HEAD)"
echo "   - Размер репозитория: $(du -sh .git | cut -f1)"
echo ""
echo "📝 Что нового в v6.0.0:"
echo "   - PostgreSQL вместо SQLite"
echo "   - Исправлены timestamp"
echo "   - Корректное отображение статусов"
echo "   - Улучшена производительность БД"