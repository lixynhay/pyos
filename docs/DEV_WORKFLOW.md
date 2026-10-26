# Рабочий процесс разработки

Документ описывает, как работать с pyos с нескольких устройств: ПК и телефон
через GitHub Codespaces.

## Источник правды

Источник правды — GitHub-репозиторий `lixynhay/pyos`, а не какая-либо
локальная копия. Если локальная копия и `origin/main` расходятся, права
`origin/main`.

Правила:

- не хранить единственный экземпляр работы только на ПК;
- перед уходом с устройства — `pytest`, `git status`, commit, push;
- не пушить секреты, токены, ключи, личные пути, снапшоты и кэши;
- не работать в одной ветке с разных устройств без `git pull`.

## ПК: крупные задачи

ПК — основная площадка для тяжёлых задач: рефакторингов, отладки, изменения
ФС/процессов/пакетного слоя.

```bash
git switch main
git pull
python -m pytest tests -q
```

Перед уходом с ПК:

```bash
python -m pytest tests -q
git status --short
git add -A
git commit -m "…"
git push
```

Если работа не закончена — всё равно закоммить и запушить в свою ветку.
Незапушенная работа существует в единственном экземпляре.

## Телефон: Codespaces

Codespaces использует `.devcontainer/devcontainer.json`
(`mcr.microsoft.com/devcontainers/python:3.11`, pytest ставится в
`postCreateCommand`).

Порядок:

1. Открыть репозиторий на GitHub → **Code** → **Codespaces** → создать
   Codespace.
2. Дождаться окончания `postCreateCommand`.
3. Проверить базу:

   ```bash
   python -m pytest tests -q
   ```

4. Создать ветку под задачу:

   ```bash
   git switch -c phone/<task>
   ```

5. Сделать маленькую правку.
6. Прогнать тесты и проверить scope diff:

   ```bash
   python -m pytest tests -q
   git diff --stat
   ```

7. Закоммитить и запушить:

   ```bash
   git add <files>
   git commit -m "…"
   git push -u origin phone/<task>
   ```

8. Открыть pull request.
9. Дождаться зелёного CI.
10. Смержить PR (вручную, после проверки). Не мержить самому, если задача
    этого не разрешает.

## CI

`.github/workflows/tests.yml` запускается на push в `main` и на каждый
pull request: Ubuntu, Python 3.11, установка pytest, `python -m pytest tests -q`.
CI — обязательная проверка перед мержем.

## Ветки

- `main` — стабильная ветка; прямой push экспериментов запрещён.
- `fix/*`, `feat/*`, `chore/*`, `phone/*` — рабочие ветки.
- Force push не используется.
- Одна ветка — одно устройство за раз. Если ветка уже открыта на другом
  устройстве, сначала `git pull`.

## Что не коммитить

`.gitignore` покрывает `venv/`, `.venv/`, `env/`, `.env*`, `*.tmp`, `*.temp`,
`*.log`, `logs/`, снапшоты (`pyos-*.json`, `*.snapshot.json`,
`snapshot-*.json`), `.mypy_cache/`, `.ruff_cache/`, `.idea/`, `.DS_Store`,
`Thumbs.db`, `desktop.ini`.

Секреты и токены не коммитятся никогда, даже в приватный репозиторий.
