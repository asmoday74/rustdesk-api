# Быстрая установка / Quick install

Пример настройки и развёртывания **rustdesk-api** вместе с `rustdesk-server`
(hbbs/hbbr) через Docker Compose.

## 1. Требования

- Docker 20.10+ и Docker Compose 2.0+
- Для генератора клиентов: репозиторий на GitHub с включёнными Actions
  (этот репозиторий) и GitHub Personal Access Token

## 2. Запуск

```bash
git clone https://github.com/asmoday74/rustdesk-api
cd rustdesk-api

# при необходимости отредактируйте переменные окружения в docker-compose.yml
docker compose up -d
```

Портал будет доступен на `http://<сервер>:21114`.

Первый вход:

| Параметр | Значение |
|----------|----------|
| URL      | `http://<сервер>:21114/login` |
| Логин    | `admin` |
| Пароль   | `admin` |

> Сразу смените пароль администратора (Пользователи → ключ) и значения
> `SECRET_KEY`, `POSTGRES_PASSWORD` в `docker-compose.yml`.

## 3. Переменные окружения (docker-compose.yml)

| Переменная | Назначение |
|------------|------------|
| `DB_DSN` | строка подключения к PostgreSQL |
| `SECRET_KEY` | секрет Flask (сессии веб-портала) |
| `TEMPLATE` | дизайн портала: `rustdesk` (по умолчанию) или `sve` |
| `CLIENTGEN_DIR` | каталог артефактов сборок (по умолчанию `/data/clientgen`) |

### Генератор клиентов (GitHub Actions)

| Переменная | Назначение |
|------------|------------|
| `GH_USER` | владелец GitHub-репозитория с воркфлоу `generator-*.yml` |
| `GH_REPO` | имя репозитория (например `rustdesk-api`) |
| `GH_BRANCH` | ветка для запуска сборок (например `main`) |
| `GH_TOKEN` | PAT с правом запускать Actions (`actions:write`) |
| `GENURL` | **публичный** URL этого сервера, доступный раннерам GitHub (например `https://rd.example.com:21114`) |
| `ZIP_PASSWORD` | пароль AES-архива с секретами сборки |
| `PROTOCOL` | `https` (по умолчанию) |
| `SH_SECRET` | необязательно: секрет self-hosted сборки Windows |

Без заполненных `GH_USER/GH_TOKEN/GENURL/ZIP_PASSWORD` кнопка «Собрать»
вернёт ошибку «Генератор не настроен».

## 4. Настройка GitHub для генератора

1. Поместите этот репозиторий на GitHub (воркфлоу `generator-*.yml`,
   `.github/actions/decrypt-secrets`, `.github/patches` уже включены).
2. В настройках репозитория **Settings → Secrets and variables → Actions**
   добавьте секреты:
   - `GENURL` — тот же публичный URL, что в `docker-compose.yml`
     (раннеры скачивают по нему зашифрованные секреты сборки и выгружают
     готовые установщики);
   - `ZIP_PASSWORD` — тот же пароль, что в `docker-compose.yml`;
   - при необходимости `SIGN_BASE_URL`/`SIGN_API_KEY` (подпись Windows),
     `MACOS_P12_*` (подпись macOS), `ANDROID_*` (подпись Android).
3. Создайте PAT в настройках профиля (https://github.com/settings/personal-access-tokens) Fine-grained и укажите его
   в `GH_TOKEN`. Необходимо выдать права **Read-Write** на **Actions** и **Workflows**

> Важно: сервер должен быть доступен из интернета по `GENURL` — воркфлоу
> GitHub обращаются к нему напрямую. За NAT без проброса порта/прокси
> сборки работать не будут.

Сборка одной платформы занимает ~30–45 минут; статус и ссылка на запуск
GitHub Actions видны в разделе «Создание клиента», готовый файл скачивается
кнопкой «Скачать».

## 5. Настройка клиентов RustDesk

В клиенте RustDesk (или в сгенерированном брендированном клиенте) укажите:

```ini
rustdesk-host=<сервер>:21115      # hbbs
api-server=http://<сервер>:21114  # этот сервер
key=<публичный ключ hbbs>
```

После этого в клиенте доступен вход по учётной записи веб-портала,
синхронизация адресной книги и вкладка «Группа».

## 6. Полезные команды

```bash
docker compose logs -f            # логи
docker compose restart            # перезапуск
docker compose down               # остановка
python tests/run_tests.py         # автономные тесты (без PostgreSQL)
```
