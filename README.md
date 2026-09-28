# AIRuntime × MAX

Витрина бизнеса с онлайн-записью **прямо в мессенджере MAX**.  
Владелец описывает услуги одним сообщением — клиент записывается по ссылке внутри MAX, заявка приходит владельцу в чат с ботом.

**Демо-бот:** [max.ru/t403_hakaton_max_bot](https://max.ru/t403_hakaton_max_bot)  
**Мини-приложение:** `https://airuntime.ru/max`  
**Команда:** Тоталисты

---

## Зачем

Микробизнес услуг (автосервис, барбершоп, репетитор, кофейня) часто живёт в чатах и сторис: нет сайта, CRM и разработчика. AIRuntime собирает рабочую витрину из одного описания и оставляет весь цикл записи внутри MAX — без установки приложений и отдельной регистрации.

Витрина — это **валидированные данные** (`ServiceConfig`), а не сгенерированный код: один мультитенантный мини-апп рендерит все сервисы. Невалидный ответ модели обрезается схемой, а не уезжает клиенту.

---

## Основной сценарий

| Кто | Что делает |
|---|---|
| **Владелец** | «Начать» в боте → «Открыть AIRuntime» → описывает бизнес → «Собрать витрину» → получает ссылку и «Поделиться» |
| **Клиент** | Открывает `max.ru/<bot>?startapp=<slug>` → выбирает услугу и время → «Записаться» |
| **Владелец** | Получает заявку в чат → «Принять» в приложении → клиенту приходит подтверждение в MAX |

Бот — только входная дверь и уведомления. Любое сообщение → приветствие с кнопкой «Открыть AIRuntime». Создание, правки, публикация и заявки — в мини-приложении.

---

## 15 пунктов из формата сдачи

Раздел повторяет список из задания трека по порядку.

1. **Назначение.** Онлайн-запись в MAX для микробизнеса услуг из одного сообщения владельца: без сайта, разработчика и CRM.
2. **Основной пользовательский сценарий.** См. таблицу выше.
3. **Состав и архитектура.** Чат-бот (вебхук) + мини-приложение (Next.js, MAX Bridge) поверх FastAPI и PostgreSQL; генератор превращает описание в `ServiceConfig`. Подробности — в разделе «Состав».
4. **Одна команда запуска.** `docker compose up --build` из корня (postgres, redis, minio, backend, worker, django-admin, frontend). Образы `codex` и `preview` закрыты профилем `build-only` и в `up` не участвуют. Сборка с нуля ~4 мин без учёта базовых образов.
5. **Параметры окружения для проверки в MAX.** На развёрнутом боте ничего настраивать не нужно. Для своего экземпляра: `MAX_BOT_TOKEN`, `MAX_BOT_USERNAME`, `MAX_WEBHOOK_SECRET`, публичные `API_URL`/`FRONTEND_URL` по HTTPS, `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`, `MAX_WIZARD_MODEL`) для генерации.
6. **Переменные окружения.** Полный список — `.env.example`, блок «MAX messenger». Рабочих секретов в репозитории нет.
7. **Порты.** `3000` — frontend / мини-приложение, `8000` — API, `8001` — Django admin, `5432` — PostgreSQL, `6379` — Redis, `9000`/`9001` — MinIO. В проде всё за Traefik на `443`.
8. **Зависимости.** `backend/requirements.txt` + `backend/requirements.lock`, `frontend/package-lock.json`.
9. **Внешние сервисы.** MAX Bot API `platform-api2.max.ru` (нужен сертификат Минцифры из `infra/certs/`), MAX Bridge `st.max.ru`, LLM через OpenAI-совместимый шлюз. Сама платформа MAX в Docker не воспроизводится: вебхук и мини-апп требуют публичный HTTPS и клиент MAX — сценарий проверяется на боте `t403_hakaton_max_bot`.
10. **Данные.** Владелец (id MAX, имя, id чата), сервис (`ServiceConfig`), заявка (услуга, время, имя, телефон при согласии, комментарий). Личность — только из подписанных стартовых параметров; в модель уходит только текст описания.
11. **Тестовые данные.** Предзагруженных нет. В мини-приложении есть примеры описаний («Автосервис», «Барбершоп», «Кофейня», «Репетитор»). Автотесты — свои фикстуры и подменённый MAX API.
12. **Пошаговая проверка.** См. «Ручная проверка» ниже.
13. **Ожидаемое поведение.** Любое сообщение боту → баннер + «Открыть AIRuntime». Описание короче 12 символов → кнопка неактивна, сервер отклоняет. Сборка → индикатор → «Витрина готова». Скрытый сервис → клиент видит «не найден», владелец — предпросмотр. Заявка → сообщение владельцу; «Принять» → подтверждение клиенту.
14. **Ограничения.** Слоты — текстовые варианты без проверки пересечений; нет оплаты и напоминаний; один владелец на сервис, до 10 сервисов; модель может ошибиться в разборе (правка текстом без модели); уведомление клиенту — если MAX разрешает боту написать ему.
15. **Остановка / повторный запуск.** `docker compose down` → `docker compose up -d`. Стереть данные: `docker compose down -v`. Прод: `docker compose -f docker-compose.prod.yml --env-file .env up -d`.

---

## Быстрый старт

```bash
cp .env.example .env
# минимум для генерации витрин: OPENAI_API_KEY (+ при необходимости OPENAI_BASE_URL)
docker compose up --build
```

| Сервис | URL |
|---|---|
| Frontend / мини-апп | http://localhost:3000 · http://localhost:3000/max |
| Backend API / OpenAPI | http://localhost:8000 · http://localhost:8000/api/v1/openapi.json |
| Django admin | http://localhost:8001 |
| MinIO | http://localhost:9001 |

Полный сценарий MAX (вебхук + мини-апп в клиенте) проверяется на развёрнутом боте — локально поднимается весь стек, но доставка вебхука и открытие мини-приложения требуют публичный HTTPS.

### Локально без Docker

```bash
# Backend
cd backend && python -m venv .venv && .venv\Scripts\activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn src.main:app --reload

# Worker (деплои / биллинг платформы)
python -m src.workers.deployment_worker

# Frontend
cd frontend && npm install && npm run dev
```

### Тесты поверхности MAX

```bash
DATABASE_URL=sqlite:///max.db pytest tests/test_max_platform.py
```

84 теста: подпись `initData` (подделка, чужой токен, TTL, дубли), клампинг ответа модели, бот как входная дверь, сквозной путь «описание → заявка → ответ клиенту» через HTTP с подменённым MAX API.

Полный набор (как в CI): Postgres + Redis, затем:

```bash
# CI / стандартные порты
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/airuntime_test
export REDIS_URL=redis://localhost:6379/0

# Локально, если Postgres/Redis уже заняты (пример для airuntime-max-pg):
# DATABASE_URL=...@localhost:55432/airuntime_test
# REDIS_URL=redis://localhost:56379/0

pytest tests -q
ruff check backend/src tests admin
ruff format --check backend/src tests admin
cd frontend && npm ci && npm run lint && npm run build
```

CI (`.github/workflows/ci.yml`) прогоняет тесты и линтеры на каждый push/PR в `main`.
При пуше в `main` дополнительно собираются и публикуются образы в GHCR
(`.github/workflows/docker.yml`):

```text
ghcr.io/airuntime-ru/airuntime-max/backend:latest
ghcr.io/airuntime-ru/airuntime-max/frontend:latest
ghcr.io/airuntime-ru/airuntime-max/admin:latest
```

Теги: `latest`, short SHA коммита, имя ветки.

---

## Состав

```text
backend/src/services/max/
  client.py       Bot API: сообщения, кнопки, вебхук
  init_data.py    проверка подписи стартовых параметров (HMAC-SHA256)
  schema.py       ServiceConfig + нормализация
  generator.py    описание → ServiceConfig (с keyword-фоллбэком)
  storefronts.py  сервисы, владельцы, ссылки
  bot.py          приветствие и уведомления о заявках
  setup.py        регистрация команд и вебхука
backend/src/api/routers/max.py   webhook + /max/miniapp/*
frontend/src/app/max/            мини-приложение
frontend/src/lib/max/            Bridge + API-клиент
frontend/src/components/max/     кабинет владельца и витрина клиента
infra/certs/                     CA Минцифры для platform-api2.max.ru
docs/max-presentation/           колода для жюри (deck.html → PDF/PPTX)
```

Остальной репозиторий — платформа AIRuntime (чат → код → Docker-деплой сайтов и Telegram-ботов). MAX-трек от неё отделён: витрина — данные, а не сгенерированный код.

### Возможности MAX сверх минимума

| Возможность | Где | Зачем |
|---|---|---|
| `WebApp.requestContact()` | форма записи | телефон из аккаунта MAX одной кнопкой |
| `WebApp.shareMaxContent()` | «Поделиться» | родной экран «Отправить в чат» |
| кнопки `open_app` + `payload` | сообщения бота | сразу в заявки / в нужный сервис |
| диплинк `?startapp=<slug>` | ссылка и QR | клиент открывает витрину без регистрации |
| профиль из `initData` | форма, вход | имя уже подставлено |
| `BackButton`, `enableClosingConfirmation` | предпросмотр, сборка | нативная навигация и защита от случайного закрытия |
| `HapticFeedback` | готовая витрина, принятая заявка | тактильный отклик |

### Две модели доверия

- **Вебхук** вызывает MAX. Доставки не подписаны → секрет в URL — вся аутентификация. Неверный секрет → `404`, не `403`; секрет не логируется.
- **Мини-апп** вызывает API из клиента. Личность — только из `X-Max-Init-Data`, проверенного по токену бота. Идентификатор пользователя из тела запроса не принимается.

У MAX ключом HMAC служит строка `WebAppData`, сообщением — токен бота (не наоборот, как в Telegram).

### Сертификат Минцифры

`platform-api2.max.ru` подписан Russian Trusted Sub CA. Без него каждый вызов API падает с `CERTIFICATE_VERIFY_FAILED`. Сертификаты в `infra/certs/`, образ бэкенда ставит их в системное хранилище.

```bash
echo | openssl s_client -connect platform-api2.max.ru:443 \
  -servername platform-api2.max.ru \
  -CAfile infra/certs/russian-trusted-ca.crt 2>/dev/null | grep "Verify return code"
```

### Подключение своего бота

1. Создайте бота на «MAX для партнёров», получите токен.
2. Заполните `MAX_BOT_TOKEN`, `MAX_BOT_USERNAME`, `MAX_WEBHOOK_SECRET` (см. `.env.example`). Нужен HTTPS.
3. `alembic upgrade head` (ревизия `0027_max_platform`).
4. Зарегистрируйте вебхук:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env \
     exec -T backend python -m src.services.max.setup --apply
   ```
5. В кабинете партнёра укажите URL мини-приложения: `https://<домен>/max`.

---

## Ручная проверка

Нужны два аккаунта MAX: владелец и клиент.

| # | Действие | Ожидаемый результат |
|---|---|---|
| 1 | Открыть [бота](https://max.ru/t403_hakaton_max_bot), «Начать» | Приветствие с баннером и «Открыть AIRuntime» |
| 2 | «Открыть AIRuntime» | Мини-приложение с полем описания |
| 3 | Описать бизнес → «Собрать витрину» | Индикатор сборки → «Витрина готова», «Поделиться», «Открыть» |
| 4 | «Открыть» | Витрина глазами клиента: услуги и цены из описания |
| 5 | Клиент открывает ссылку со второго аккаунта | Тот же сервис внутри MAX |
| 6 | Услуга + время → «Записаться» | Экран «заявка принята» |
| 7 | — | Владельцу в чат: «Новая заявка» → «Открыть заявки» |
| 8 | «Принять» | Клиенту в MAX: «Запись подтверждена» |

Пример описания для шага 3:

> Автосервис на Лесной. Диагностика 1500, замена масла 900, шиномонтаж 2400. Работаем с 9 до 20

---

## Презентация

Исходники и сборка — `docs/max-presentation/`:

```bash
cd docs/max-presentation
python stamp-commit.py          # commit hash на слайд 1 + пересборка
python build-pdf.py             # presentation.pdf (без секретов)
MAX_BOT_TOKEN=... python build-pdf.py --jury   # PDF для жюри с токенами
```

Перед сдачей заполнить название команды и участников на слайде 2; jury-PDF с токенами **не коммитить**.

---

## Структура репозитория

```text
backend/     FastAPI: API, воркер, агент, MAX-сервисы
frontend/    Next.js: кабинет, лендинг, мини-приложение /max
admin/       Django admin (те же таблицы Postgres)
infra/       Traefik, почта, сертификаты Минцифры
tests/       pytest (включая test_max_platform.py)
docs/        презентация MAX и вспомогательные заметки
shared/      общие схемы (задел)
```

Стек платформы: FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL · Redis · Next.js · Tailwind · MinIO · Docker.

---

## Лицензия и секреты

В репозитории нет рабочих токенов и ключей. Значения для своего запуска — только в локальном `.env` (шаблон `.env.example`). Jury-PDF с подставленными секретами передаётся организаторам отдельно и в git не попадает.
