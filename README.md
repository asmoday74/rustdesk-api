# rustdesk-api — RustDesk API / Monitor

[![Docker](https://img.shields.io/badge/docker-ready-blue)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-15-blue)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 🇷🇺 Описание

**rustdesk-api** — сервер сопровождения самостоятельной инсталляции
[RustDesk](https://rustdesk.com): API-сервер для клиентов, веб-портал
администрирования и встроенный генератор брендированных клиентов.
Работает в связке с открытым `rustdesk-server` (hbbs/hbbr), полностью
совместим с клиентами RustDesk 1.4.x.

### Возможности

- **Устройства** — список всех устройств с пагинацией, поиском и фильтрами,
  статус онлайн/оффлайн, информация об устройстве, подключение по ID,
  удаление (администратор).
- **Списки устройств** — адресные книги клиентов RustDesk:
  - вкладка **«Адресная книга»**: личные и общие адресные книги, записи,
    теги с цветами, правила доступа (чтение / чтение и запись / полный доступ);
  - вкладка **«Коллекции»**: коллекции (группы) пользователей — обычные и
    общие, как в rustdesk-server-pro.
- **Пользователи** — создание/удаление, роли (администратор/пользователь),
  смена пароля, назначение группы.
- **Аудит безопасности** — журнал соединений и передачи файлов клиентов
  (с пагинацией).
- **Создание клиента** — встроенный генератор кастомных клиентов RustDesk
  (порт [rdgen](https://github.com/bryangerlach/rdgen)): форма настройки
  (ОС, версия, имя приложения, свой сервер host/key/api, иконка/логотип,
  постоянный пароль, default/override-настройки), сборка через GitHub
  Actions (Windows / Windows x86 / Linux / macOS / Android) и скачивание
  готовых установщиков прямо из веб-интерфейса. Без отдельного сервера
  rdgen и без rdgen-cli.
- **Мультиязычность** — русский и английский интерфейс (переключатель в шапке).
- **Оформление** — два дизайна (`rustdesk` с тёмной/светлой схемой и `sve`),
  выбор переменной `TEMPLATE`.
- **Совместимость с клиентом 1.4.x** — логин клиентов с выдачей
  `access_token`, синхронизация адресной книги, вкладка «Группа», аудит
  подключений.

### Развёртывание

Краткая инструкция: [quick_install.md](quick_install.md).

---

## 🇬🇧 Description

**rustdesk-api** is a companion server for a self-hosted
[RustDesk](https://rustdesk.com) installation: an API server for clients,
an administration web portal, and a built-in branded client generator.
It works together with the open-source `rustdesk-server` (hbbs/hbbr) and is
fully compatible with RustDesk 1.4.x clients.

### Features

- **Devices** — paginated list of all devices with search and filters,
  online/offline status, device info, one-click connect by ID,
  deletion (administrators).
- **Device lists** — RustDesk client address books:
  - **Address book** tab: personal and shared address books, records,
    colored tags, access rules (read / read-write / full access);
  - **Collections** tab: user collections (groups) — default and shared,
    like in rustdesk-server-pro.
- **Users** — create/delete, roles (admin/user), password change,
  group assignment.
- **Security audit** — connection and file transfer logs (paginated).
- **Client generation** — built-in custom RustDesk client generator
  (ported from [rdgen](https://github.com/bryangerlach/rdgen)): a
  configuration form (OS, version, app name, custom server host/key/api,
  icon/logo, permanent password, default/override settings), builds via
  GitHub Actions (Windows / Windows x86 / Linux / macOS / Android) and
  downloads of the finished installers right from the web UI. No separate
  rdgen server and no rdgen-cli required.
- **Multilingual UI** — Russian and English (switcher in the header).
- **Themes** — two designs (`rustdesk` with dark/light scheme and `sve`),
  selected via the `TEMPLATE` variable.
- **Client 1.4.x compatibility** — client login issuing `access_token`,
  address book sync, the "Group" tab, connection audit.

### Deployment

Quick start guide: [quick_install.md](quick_install.md).

---

## 🙏 Спасибо за идеи и помощь / Credits

- [fiverok/sveApiRust](https://github.com/fiverok/sveApiRust) — исходный
  проект мониторинга (Flask), из которого вырос этот сервер / the original
  Flask monitoring project this server grew from
- [AlekseyLapunov/rdgen-cli](https://github.com/AlekseyLapunov/rdgen-cli) —
  консольный генератор клиентов, чья логика легла в основу встроенного
  генератора / the CLI client generator whose logic inspired the built-in one
- [bryangerlach/rdgen](https://github.com/bryangerlach/rdgen) — генератор
  кастомных клиентов RustDesk и GitHub-воркфлоу сборки, портированные в этот
  репозиторий / the custom RustDesk client generator and build workflows
  ported into this repository
- [lejianwen/rustdesk-server](https://github.com/lejianwen/rustdesk-server) —
  форк rustdesk-server с поддержкой `KeyExchange` / the rustdesk-server fork
  with `KeyExchange` support

---

## 📝 Лицензия / License

MIT License. См. [LICENSE](LICENSE) / See [LICENSE](LICENSE).
