# Публикация в GitHub: rustdesk-api + rustdesk-hdds-patch

Проект выгружается в **asmoday74/rustdesk-api**, патч hbbs — в
**asmoday74/rustdesk-hdds-patch**.

Локально:
- Проект: `C:\Users\asm\Documents\ZCode\rustdesk-api`
- Патч hbbs: `C:\Users\asm\Documents\ZCode\rustdesk-hdds-patch`

Ремонты уже настроены:
- `rustdesk-api`  → `https://github.com/asmoday74/rustdesk-api.git`
- `rustdesk-hdds-patch` → `https://github.com/asmoday74/rustdesk-hdds-patch.git`

> Репозитории создайте на GitHub **пустыми** (без README/.gitignore).
> `gh` не установлен — создавайте через веб или установите `gh`.

## rustdesk-hdds-patch (патч hbbs)

```bash
cd C:\Users\asm\Documents\ZCode\rustdesk-hdds-patch
git add .
git commit -m "KeyExchange patch for official rustdesk-server (hbbs)"
git branch -M main
git push -u origin main
```
(`git init` и `remote add origin` уже выполнены.)

## rustdesk-api (проект)

```bash
cd C:\Users\asm\Documents\ZCode\rustdesk-api
git add .
git commit -m "rustdesk-api: RustDesk API + address book + groups + audit + UI templates"
git branch -M main
git push -u origin main
```

> Если `git push` ругается, что upstream уже есть: `git push -u origin main --force`
> используйте ТОЛЬКО если уверены, что в новом репозитории нет нужной истории.

## Если «remote origin already exists»

Это значит remote уже есть (остался от клона fiverok/sveApiRust). Вместо
`git remote add origin ...` используйте:

```bash
git remote set-url origin https://github.com/asmoday74/rustdesk-api.git
```

## Проверка

```bash
git -C C:\Users\asm\Documents\ZCode\rustdesk-api remote -v
git -C C:\Users\asm\Documents\ZCode\rustdesk-hdds-patch remote -v
```
