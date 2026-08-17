
# asmApiRD — RustDesk API / Monitor

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

### Адресная книга (порт функционала lejianwen/rustdesk-api)
- **Синхронизация адресной книги** клиентов RustDesk с сервером
- **Авторизация клиентов** по логину/паролю с выдачей access_token
- **Личные адресные книги** для каждого пользователя
- **Общие адресные книги** (коллекции) с правилами доступа: чтение / чтение и запись / полный доступ
- **Теги** с цветами, переименованием и удалением
- **Legacy-режим** (`GET/POST /api/ab`) и **personal-режим** (guid-эндпоинты)
- **Веб-интерфейс** управления адресной книгой (`/ab`)

### Группы пользователей (как в rustdesk-server-pro)
- **Группы** двух типов: *обычная* (участник видит только себя) и *общая*
  (участники видят устройства друг друга во вкладке «Группа» клиента)
- **Назначение группы** пользователю при создании и в любой момент после
- **Вкладка «Группа» в клиенте RustDesk**: `/api/users` и `/api/peers` отдают
  участников и их устройства согласно членству в группе
- **Шаринг адресной книги с группой** (правила доступа type=group)
- **Управление группами** в админ-панели (`/admin`)

### Оформление (шаблоны дизайна)
- Шаблоны разнесены по папкам: `templates/<design>/` — страницы (`base/index/admin/ab/login`)
  + своя статика (`static/css/design.css`, `static/img/logo.svg`). Общий контент/JS —
  в `templates/_partials/`, общие стили — `static/css/base.css`.
- **`rustdesk`** (по умолчанию): стиль консоли RustDesk — тёмная/светлая схема
  (переключатель в шапке, `localStorage`), родной логотип, сайдбар.
- **`sve`**: прежний фирменный стиль (зелёный #004D43 + жёлтый #FFC700), **светлый фон**,
  без переключателя темы.
- Выбор шаблона — переменная **`TEMPLATE`** в `docker-compose.yml`. Если не задана или
  папка `templates/<имя>` отсутствует — используется `rustdesk`.
- Статика шаблона отдаётся роутом `/theme/<path>`.
- Раздел **«Управление»** (`/admin`) разбит на вкладки: Пользователи / Группы /
  Устройства (подменю).

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
- **Шаблоны дизайна**: `rustdesk` (тёмная/светлая схема) и `sve` (прежний от https://github.com/fiverok/sveApiRust)
- **Автоматическая очистка** старых данных

## 🚀 Быстрый старт

### Предварительные требования
- Docker 20.10+
- Docker Compose 2.0+
- Git
- PostgreSQL 15 (автоматически поднимается в контейнере)

### Установка

```bash
# Клонирование репозитория
git clone https://github.com/asmoday74/rustdesk-api
cd rustdesk-api

# Запуск в Docker
docker-compose up -d

# Проверка работы
curl http://localhost:21114/health
```

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
| `/api/audit/conn` | POST | Аудит подключений (клиент >= 1.3) | пустой 200 |
| `/api/audit/file` | POST | Аудит файловых операций | пустой 200 |
| `/api/audit/alarm` | POST | Аудит тревог | пустой 200 |
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

### API Адресной книги (для клиентов RustDesk)

Авторизация клиентов: `POST /api/login` с полями `uuid`/`deviceInfo` возвращает
`access_token`, который клиент передаёт в заголовке `Authorization: Bearer <token>`.

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/login-options` | GET | Опции входа (пустой список) |
| `/api/user/info`, `/api/currentUser` | GET/POST | Информация о текущем пользователе |
| `/api/logout` | POST | Выход клиента (удаление токена) |
| `/api/users` | GET | Пользователи группы (Bearer) |
| `/api/peers` | GET | Устройства группы |
| `/api/ab` | GET/POST | Legacy: получить/полностью обновить адресную книгу |
| `/api/ab/personal` | POST | Guid личной адресной книги |
| `/api/ab/settings` | POST | Настройки (лимиты) |
| `/api/ab/shared/profiles` | POST | Доступные общие адресные книги |
| `/api/ab/peers?ab={guid}` | POST | Список записей адресной книги |
| `/api/ab/tags/{guid}` | POST | Список тегов |
| `/api/ab/peer/add/{guid}` | POST | Добавить запись |
| `/api/ab/peer/{guid}` | DELETE | Удалить записи |
| `/api/ab/peer/update/{guid}` | PUT | Обновить запись |
| `/api/ab/tag/add/{guid}` | POST | Добавить тег |
| `/api/ab/tag/rename/{guid}` | PUT | Переименовать тег |
| `/api/ab/tag/update/{guid}` | PUT | Изменить цвет тега |
| `/api/ab/tag/{guid}` | DELETE | Удалить теги |

### API Адресной книги (веб-интерфейс, сессия)

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/web/ab/collections` | GET/POST | Список/создание общих книг |
| `/api/web/ab/collections/{id}` | PUT/DELETE | Переименование/удаление |
| `/api/web/ab/rules/{collection_id}` | GET | Правила доступа коллекции |
| `/api/web/ab/rules` | POST | Предоставить/изменить доступ |
| `/api/web/ab/rules/{id}` | DELETE | Отозвать доступ |
| `/api/web/ab/users` | GET | Пользователи группы |

### API Аудита безопасности (веб, сессия; admin)
- `GET /api/web/audit/conn` — журнал соединений (из `rustdesk_audits`, тип `conn`).
- `GET /api/web/audit/file` — журнал передачи файлов (тип `file`).
- Данные пишутся клиентами в `POST /api/audit/conn|file|alarm`.
- **«Аудит безопасности»** — отдельный раздел в корне меню (`/audit`, admin), две вкладки:
  «Журнал соединений» (Локальный←откуда, Удаленный←куда, время начала/окончания, тип 0–5)
  и «Журнал передачи файлов» (Локальный, Удаленный, время, направление →/←, подробности:
  один файл — имя+размер, несколько — всплывающий список). Обе с пагинацией.
- В «Управление» вкладка «Группы» переименована в **«Коллекции»**.

### API Групп (веб-интерфейс, сессия; управление — admin)

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/web/groups` | GET | Список групп |
| `/api/web/groups` | POST | Создать группу (`name`, `type`: 1/2) |
| `/api/web/groups/{id}` | PUT | Изменить группу |
| `/api/web/groups/{id}` | DELETE | Удалить группу (нельзя id=1) |
| `/api/web/users/{id}/group` | PUT | Назначить пользователю группу |

## 📁 Структура проекта

```
sveApiRust/
├── static/                 # Статические файлы
│   ├── style.css          # Общие стили
│   ├── login.html         # Страница входа
│   ├── index.html         # Страница мониторинга
│   ├── admin.html         # Панель администратора
│   ├── ab.html            # Адресная книга (веб-интерфейс)
│   └── favicon.svg        # Иконка сайта
├── modules/               # Модули приложения
│   ├── __init__.py
│   ├── database.py        # Работа с БД (PostgreSQL)
│   ├── auth.py            # Аутентификация
│   ├── api_auth.py        # API аутентификации
│   ├── api_computers.py   # API устройств
│   ├── api_public.py      # Публичные API
│   ├── ab.py              # Адресная книга: сервисный слой
│   └── api_ab.py          # Адресная книга: API эндпоинты
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
| `TOKEN_EXPIRE_SECONDS` | Время жизни токена клиента (сек) | `604800` (7 дней) |
| `AB_PERSONAL` | Режим личных адресных книг (1/0) | `1` |
| `UI_TEMPLATE` | Шаблон дизайна (`rustdesk`\|`sve`) | `rustdesk` |

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

### Адресная книга в клиенте RustDesk

После указания `api-server` войдите в клиенте RustDesk под учётной записью
(логин/пароль из веб-панели, например `admin`/`admin`). Адресная книга будет
синхронизироваться с сервером автоматически. Управление записями, тегами и
общими адресными книгами также доступно в веб-интерфейсе на странице `/ab`.

Протокол проверен по исходникам клиента RustDesk 1.4.x: поддерживаются поля
`note` и `device_group_name`, `forceAlwaysRelay` возвращается строкой,
`/api/ab/peers` постраничный (`current`/`pageSize`). Сервер работает в связке
с открытым `rustdesk-server` (hbbs/hbbr): порт 21114 hbbs не занимает и
отдаёт внешнему API-серверу.

## 🐳 Развертывание

Полная инструкция для **Oracle Linux 9 (minimal)** — связка rustdesk-server
(hbbs/hbbr) + sveApiRust, firewall, SELinux, бэкапы — в
[docs/oracle-linux-9.md](docs/oracle-linux-9.md).

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

### Миграция с rustdesk-server-pro (sqlite3 → PostgreSQL)

Скрипт `migrate_pro_to_pg.py` переносит из `db.sqlite3` (pro): пользователей,
группы, устройства, адресные книги/коллекции/теги/правила и аудит
(соединения/файлы).

```bash
pip install psycopg2-binary
# сначала поднимите стек один раз, чтобы приложение создало таблицы
python migrate_pro_to_pg.py /path/to/db.sqlite3 \
    postgresql://rustdesk:rustdesk@<host>:5432/rustdesk_monitor
```

Миграция идемпотентна по логинам (существующие пользователи, напр. admin, не
дублируются — используется их id). Запускайте после первого старта приложения.

> Пароли pro перенести нельзя: pro хешировал их с внутренним преобразованием
> (bcrypt не проходит проверку), а bcrypt в проекте отключён для совместимости
> со sveApiRust (только PBKDF2). После миграции сбросьте пароли pro-пользователей
> (через админку или `hash_password`), затем смените после входа.

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

### Тесты

Автономный набор тестов (не требует PostgreSQL — слой БД подменяется
in-memory SQLite). Покрывает адресную книгу, теги, коллекции, правила
доступа, группы пользователей, аудит клиентов и авторизацию:

```bash
python tests/run_tests.py
```

Успешный завершение — строка `ALL N CHECKS PASSED` и код возврата 0.

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