#!/bin/bash

# quick-commit.sh - Скрипт для быстрого коммита изменений
# Использование: ./quick-commit.sh "Сообщение коммита"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода справки
show_help() {
    echo -e "${BLUE}Использование:${NC}"
    echo "  ./quick-commit.sh \"Сообщение коммита\""
    echo ""
    echo -e "${BLUE}Примеры:${NC}"
    echo "  ./quick-commit.sh \"fix: исправлена ошибка авторизации\""
    echo "  ./quick-commit.sh \"feat: добавлена новая функция\""
    echo ""
    echo -e "${BLUE}Типы коммитов:${NC}"
    echo "  feat     - Новая функциональность"
    echo "  fix      - Исправление ошибки"
    echo "  docs     - Изменения в документации"
    echo "  style    - Форматирование кода"
    echo "  refactor - Рефакторинг"
    echo "  perf     - Улучшение производительности"
    echo "  test     - Добавление/изменение тестов"
    echo "  chore    - Обслуживание (зависимости, конфиги)"
}

# Проверка аргументов
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    show_help
    exit 0
fi

# Проверяем, есть ли сообщение коммита
if [ -z "$1" ]; then
    echo -e "${RED}❌ Ошибка: не указано сообщение коммита${NC}"
    echo "Использование: ./quick-commit.sh \"Сообщение коммита\""
    echo "Для справки: ./quick-commit.sh --help"
    exit 1
fi

COMMIT_MESSAGE="$1"

# Проверяем, что мы в Git репозитории
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    echo -e "${RED}❌ Ошибка: не в Git репозитории${NC}"
    exit 1
fi

# Проверяем, есть ли изменения
if [[ -z $(git status -s) ]]; then
    echo -e "${YELLOW}⚠️  Нет изменений для коммита${NC}"
    exit 0
fi

echo -e "${BLUE}📝 Текущие изменения:${NC}"
git status -s
echo ""

# Показываем список измененных файлов
echo -e "${BLUE}📄 Измененные файлы:${NC}"
git diff --name-only
echo ""

# Спрашиваем, нужно ли добавить все файлы
echo -e "${YELLOW}Добавить все файлы? (y/n) [y]:${NC} "
read -r add_all

if [[ "$add_all" =~ ^[Nn]$ ]]; then
    echo -e "${YELLOW}Добавьте файлы вручную:${NC}"
    echo "  git add <file1> <file2> ..."
    echo "  Затем запустите скрипт снова"
    exit 0
fi

# Добавляем все изменения
echo -e "${BLUE}📦 Добавляем файлы...${NC}"
git add .

# Проверяем, есть ли что коммитить
if git diff --cached --quiet; then
    echo -e "${YELLOW}⚠️  Нет файлов для коммита (возможно, все в .gitignore)${NC}"
    exit 0
fi

# Создаем коммит
echo -e "${BLUE}📝 Создаем коммит...${NC}"
git commit -m "$COMMIT_MESSAGE"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Коммит создан успешно!${NC}"
    
    # Проверяем настройки remote
    if git remote | grep -q origin; then
        echo -e "${BLUE}⬆️ Отправить изменения на GitHub? (y/n) [y]:${NC} "
        read -r push_changes
        
        if [[ ! "$push_changes" =~ ^[Nn]$ ]]; then
            echo -e "${BLUE}⬆️ Отправляем на GitHub...${NC}"
            git push
            
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}✅ Изменения отправлены на GitHub!${NC}"
            else
                echo -e "${RED}❌ Ошибка при отправке на GitHub${NC}"
            fi
        else
            echo -e "${YELLOW}⏸️  Изменения сохранены локально, push отменен${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Remote 'origin' не настроен.${NC}"
        echo "Добавьте remote: git remote add origin https://github.com/fiverok/sveApiRust.git"
    fi
else
    echo -e "${RED}❌ Ошибка при создании коммита${NC}"
    exit 1
fi

# Показываем последние коммиты
echo ""
echo -e "${BLUE}📊 Последние коммиты:${NC}"
git log --oneline -3