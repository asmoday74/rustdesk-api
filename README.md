
# RustDesk Monitor

[![GitHub release](https://img.shields.io/badge/release-v6.0.0-brightgreen)](https://github.com/fiverok/sveApiRust/releases)
[![Docker](https://img.shields.io/badge/docker-ready-blue)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-15-blue)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Система мониторинга компьютеров для RustDesk с веб-интерфейсом, авторизацией и панелью администратора.

## 📋 Оглавление

- [Возможности](#-возможности)
- [Быстрый старт](#-быстрый-старт)
- [API Endpoints](#-api-endpoints)
- [Структура проекта](#-структура-проекта)
- [Конфигурация](#-конфигурация)
- [Развертывание](#-развертывание)
- [Управление пользователями](#-управление-пользователями)
- [Логирование](#-логирование)
- [Устранение неполадок](#-устранение-неполадок)
- [Разработка](#-разработка)
- [Лицензия](#-лицензия)

## 🚀 Возможности

### Основные функции
- **Мониторинг устройств** в реальном времени
- **Автоматическая регистрация** клиентов RustDesk
- **Online/Offline статус** с автоматическим обновлением
- **Поиск и фильтрация** устройств
- **Копирование имени** устройства в буфер обмена
- **Подключение через RustDesk** по клику на ID

### Администрирование
- **Управление пользователями** (создание/удаление)
- **Ролевая модель** (администратор/пользователь)
- **Удаление устройств** из системы
- **Лог аудита** всех действий
- **Статистика** онлайн/оффлайн устройств

### Технические особенности
- **PostgreSQL** для надежного хранения данных (поддерживается пул соединений)
- **Docker** контейнеризация
- **Безопасное хеширование** паролей (PBKDF2)
- **Детальное логирование** sysinfo и heartbeat
- **Корпоративный дизайн** (зеленый #004D43, желтый #FFC700)
- **Автоматическая очистка** старых данных

## 🚀 Быстрый старт

### Предварительные требования
- Docker 20.10+
- Docker Compose 2.0+
- Git
- PostgreSQL 15 (автоматически поднимается в контейнере)

### Установка


# Клонирование репозитория
git clone https://github.com/fiverok/sveApiRust.git
cd sveApiRust

# Запуск в Docker
docker-compose up -d

# Проверка работы
curl http://localhost:21114/health


### Первый вход

| Параметр | Значение |
|----------|----------|
| URL | `http://your-server:21114` |
| Логин | `admin` |
| Пароль | `admin` |

## 📡 API Endpoints

### Публичные API (для клиентов RustDesk)

| Endpoint | Метод | Описание | Ответ |
|----------|-------|----------|-------|
| `/api/sysinfo` | POST | Регистрация устройства | `SYSINFO_UPDATED` |
| `/api/heartbeat` | POST | Обновление статуса | `{"modified_at": timestamp}` |
| `/api/version` | GET | Версия API | `2.0.0` |
| `/api/sysinfo_ver` | POST | Версия сервера | `2025.1.0` |
| `/health` | GET | Проверка здоровья | `{"status": "healthy"}` |

### API Аутентификации

| Endpoint | Метод | Описание | Требует auth |
|----------|-------|----------|--------------|
| `/api/login` | POST | Вход в систему | Нет |
| `/api/users/me` | GET | Текущий пользователь | Да |
| `/api/users` | GET | Список пользователей | Admin |
| `/api/users` | POST | Создать пользователя | Admin |
| `/api/users/{id}` | DELETE | Удалить пользователя | Admin |
| `/api/users/{id}/password` | PUT | Смена пароля (v6.0.0) | Admin |

### API Управления

| Endpoint | Метод | Описание | Требует auth |
|----------|-------|----------|--------------|
| `/api/computers` | GET | Список устройств | Да |
| `/api/computers/{uuid}` | DELETE | Удалить устройство | Admin |
| `/api/stats` | GET | Статистика | Да |
| `/api/audit` | GET | Лог аудита | Admin |
| `/api/db/health` | GET | Диагностика БД | Да |
| `/api/db/repair` | POST | Восстановление БД | Admin |

## 📁 Структура проекта


sveApiRust/
├── static/                 # Статические файлы
│   ├── style.css          # Общие стили
│   ├── login.html         # Страница входа
│   ├── index.html         # Страница мониторинга
│   ├── admin.html         # Панель администратора
│   └── favicon.svg        # Иконка сайта
├── modules/               # Модули приложения
│   ├── __init__.py
│   ├── database.py        # Работа с БД (PostgreSQL)
│   ├── auth.py            # Аутентификация
│   ├── api_auth.py        # API аутентификации
│   ├── api_computers.py   # API устройств
│   └── api_public.py      # Публичные API
├── data/                  # Данные (логи)
│   ├── sysinfo.log        # Логи регистрации
│   ├── heartbeat.log      # Логи heartbeat
│   └── errors.log         # Логи ошибок
├── postgres_data/         # Данные PostgreSQL (создается автоматически)
├── app.py                 # Основной файл
├── requirements.txt       # Зависимости Python
├── Dockerfile            # Docker образ
├── docker-compose.yml    # Docker Compose
├── migrate_data.py       # Скрипт миграции из SQLite
└── README.md             # Документация
```

## ⚙️ Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `SECRET_KEY` | Секретный ключ Flask | Генерируется автоматически |
| `DB_DSN` | Строка подключения к PostgreSQL | `postgresql://rustdesk:rustdesk@db:5432/rustdesk_monitor` |
| `POSTGRES_USER` | Пользователь PostgreSQL | `rustdesk` |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL | `rustdesk` |
| `POSTGRES_DB` | Имя базы данных | `rustdesk_monitor` |

### Настройка клиентов RustDesk

В конфигурации клиента RustDesk укажите API сервер:

```ini
api-server=http://your-server:21114
```

Пример полной конфигурации:
```ini
rustdesk-host=your-server:21115
api-server=http://your-server:21114
key=your_public_key
```

## 🐳 Развертывание

### Docker Compose (рекомендуется)

```bash
# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Просмотр логов
docker-compose logs -f

# Перезапуск
docker-compose restart

# Полная очистка с удалением данных
docker-compose down -v
```

### Миграция с SQLite на PostgreSQL

Если у вас есть данные в SQLite:

```bash
# 1. Скопировать файл SQLite в контейнер
docker cp data/computers.db rustdesk-monitor:/data/computers.db

# 2. Запустить миграцию
docker exec -it rustdesk-monitor python3 migrate_data.py

# 3. Проверить данные
curl http://localhost:21114/api/stats
```

## 👥 Управление пользователями

### Создание пользователя (администратор)

```bash
curl -X POST http://localhost:21114/api/users \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newuser",
    "password": "password123",
    "role": "user",
    "email": "user@example.com"
  }'
```

### Смена пароля пользователя

```bash
curl -X PUT http://localhost:21114/api/users/1/password \
  -H "Content-Type: application/json" \
  -d '{"new_password": "newpassword123"}'
```

### Удаление пользователя

```bash
curl -X DELETE http://localhost:21114/api/users/1
```

## 📊 Логирование

### Файлы логов

| Файл | Описание | Ротация |
|------|----------|---------|
| `/data/sysinfo.log` | Регистрация устройств | 10 MB, 10 файлов |
| `/data/heartbeat.log` | Heartbeat запросы | 10 MB, 5 файлов |
| `/data/errors.log` | Ошибки приложения | 10 MB, 5 файлов |

### Просмотр логов

```bash
# Через Docker
docker exec -it rustdesk-monitor cat /data/sysinfo.log
docker exec -it rustdesk-monitor tail -f /data/heartbeat.log

# Через API (требует аутентификации)
curl http://localhost:21114/api/logs/sysinfo
curl http://localhost:21114/api/logs/heartbeat
```

## 🔧 Устранение неполадок

### Контейнер не запускается

```bash
# Проверка логов
docker logs rustdesk-monitor

# Проверка порта
netstat -tlnp | grep 21114

# Проверка статуса PostgreSQL
docker logs rustdesk-db
```

### Ошибка подключения к БД

```bash
# Проверка подключения к PostgreSQL
docker exec -it rustdesk-monitor python3 -c "
import psycopg2
import os
conn = psycopg2.connect(os.environ.get('DB_DSN'))
print('✅ Connected!')
conn.close()
"

# Проверка данных в БД
docker exec -it rustdesk-db psql -U rustdesk -d rustdesk_monitor -c "\dt"
```

### Heartbeat не обновляется

```bash
# Проверка логов heartbeat
docker exec -it rustdesk-monitor tail -f /data/heartbeat.log

# Проверка статуса устройств
curl http://localhost:21114/api/computers

# Ручная отправка heartbeat
curl -X POST http://localhost:21114/api/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"id":"test-001","uuid":"test-uuid","ver":1}'
```

### Забыли пароль администратора

```bash
# Сброс пароля через консоль
docker exec -it rustdesk-monitor python3 -c "
from modules.auth import hash_password
from modules.database import execute_query
new_hash = hash_password('new_password')
execute_query('UPDATE users SET password_hash = %s WHERE username = %s', (new_hash, 'admin'))
print('✅ Password reset to: new_password')
"
```

### Диагностика базы данных

```bash
# Проверка состояния БД
curl http://localhost:21114/api/db/health

# Принудительное восстановление
curl -X POST http://localhost:21114/api/db/repair
```

## 💻 Разработка

### Запуск в режиме разработки

```bash
# Установка зависимостей
pip install -r requirements.txt

# Создание директорий
mkdir -p data static

# Экспорт переменной для локальной БД
export DB_DSN=postgresql://user:password@localhost:5432/rustdesk_monitor

# Запуск с отладкой
export FLASK_DEBUG=1
python app.py
```

### Добавление новых API эндпоинтов

1. Создайте новый файл в `modules/` или добавьте в существующий
2. Импортируйте необходимые функции из других модулей
3. Создайте функцию инициализации маршрутов
4. Вызовите функцию в `app.py`

### Сборка Docker образа

```bash
# Сборка с тегом
docker build -t rustdesk-monitor:latest .

# Сборка с версией
docker build -t rustdesk-monitor:v6.0.0 .
```

## 📝 Лицензия

MIT License. См. файл [LICENSE](LICENSE) для деталей.

## 🤝 Вклад в проект

Приветствуются pull requests. Для крупных изменений, пожалуйста, откройте issue для обсуждения.

1. Fork репозитория
2. Создайте ветку для фичи (`git checkout -b feature/AmazingFeature`)
3. Commit изменений (`git commit -m 'Add some AmazingFeature'`)
4. Push в ветку (`git push origin feature/AmazingFeature`)
5. Откройте Pull Request

## 📧 Контакты

- **Автор**: kapalkin Artem (fiverok)
- **GitHub**: [https://github.com/fiverok/sveApiRust](https://github.com/fiverok/sveApiRust)

## 🙏 Благодарности

- RustDesk за отличный продукт
- Flask за прекрасный фреймворк
- PostgreSQL за надежную БД

---

<div align="center">
  <sub>Built with ❤️ for НПАО «Светогорский ЦБК»</sub>
</div>
```

## Основные изменения в README:

1. **Обновлена информация о версии**: указана версия `v6.0.0` и добавлен бейдж PostgreSQL.
2. **Добавлено описание миграции**: появился отдельный раздел с инструкцией по переходу с SQLite на PostgreSQL.
3. **Добавлены новые API эндпоинты**: `/api/db/health`, `/api/db/repair` и `/api/users/{id}/password`.
4. **Обновлена структура проекта**: добавлена папка `postgres_data` и файл `migrate_data.py`.
5. **Добавлены переменные окружения**: описаны переменные для работы с PostgreSQL.
6. **Расширен раздел устранения неполадок**: добавлены команды для диагностики PostgreSQL.
7. **Убрана ошибка**: убрано упоминание SQLite как основного хранилища данных.