# AIRuntime × MAX

Витрина бизнеса с онлайн-записью **прямо в мессенджере MAX**.  
Владелец описывает услуги одним сообщением — клиент записывается по ссылке внутри MAX, заявка приходит владельцу в чат с ботом.

**Демо-бот:** [max.ru/t403_hakaton_max_bot](https://max.ru/t403_hakaton_max_bot)  
**Мини-приложение:** `https://airuntime.ru/max`  
**Команда:** Тоталисты

Витрина — это валидированные данные (`ServiceConfig`), а не сгенерированный код: один мультитенантный мини-апп рендерит все сервисы.

## Сценарий

| Кто | Что делает |
|---|---|
| **Владелец** | «Начать» в боте → «Открыть AIRuntime» → описывает бизнес → «Собрать витрину» → получает ссылку и «Поделиться» |
| **Клиент** | Открывает `max.ru/<bot>?startapp=<slug>` → выбирает услугу и время → «Записаться» |
| **Владелец** | Получает заявку в чат → «Принять» в приложении → клиенту приходит подтверждение в MAX |

Бот — входная дверь и уведомления. Создание, правки, публикация и заявки — в мини-приложении.

## Быстрый старт

```bash
cp .env.example .env
# минимум: OPENAI_API_KEY (+ при необходимости OPENAI_BASE_URL)
docker compose up --build
```

| Сервис | URL |
|---|---|
| Frontend / мини-апп | http://localhost:3000 · `/max` |
| Backend API | http://localhost:8000 |
| Django admin | http://localhost:8001 |

Переменные — `.env.example` (блок «MAX messenger»). Секретов в репозитории нет.  
Полный сценарий MAX проверяется на развёрнутом боте (нужны публичный HTTPS и клиент MAX).

```bash
pytest tests/test_max_platform.py -q          # поверхность MAX (SQLite: DATABASE_URL=sqlite:///max.db)
pytest tests -q                               # полный набор (Postgres + Redis)
ruff check backend/src tests admin
cd frontend && npm ci && npm run lint && npm run build
```

При пуше в `main` CI гоняет тесты; образы уходят в GHCR: `backend`, `frontend`, `admin`.

## Состав

```text
backend/src/services/max/   клиент Bot API, initData, ServiceConfig, генератор, бот
backend/src/api/routers/max.py
frontend/src/app/max/       мини-приложение
frontend/src/lib/max/       Bridge + API
frontend/src/components/max/
infra/certs/                CA Минцифры для platform-api2.max.ru
docs/max-presentation/      колода для жюри
```

**Доверие:** вебхук аутентифицируется секретом в URL; мини-апп — только через подписанный `X-Max-Init-Data`.  
**MAX сверх минимума:** `requestContact`, `shareMaxContent`, `open_app` + payload, диплинк `?startapp=`, BackButton, HapticFeedback.

## Ручная проверка

Два аккаунта MAX: владелец и клиент.

| # | Действие | Ожидаемый результат |
|---|---|---|
| 1 | [Бот](https://max.ru/t403_hakaton_max_bot) → «Начать» | Баннер и «Открыть AIRuntime» |
| 2 | «Открыть AIRuntime» → описать бизнес → «Собрать витрину» | «Витрина готова», «Поделиться» |
| 3 | Клиент открывает ссылку → услуга + время → «Записаться» | Заявка принята |
| 4 | Владелец: «Открыть заявки» → «Принять» | Клиенту: «Запись подтверждена» |

Пример: *Автосервис на Лесной. Диагностика 1500, замена масла 900, шиномонтаж 2400. Работаем с 9 до 20*

## Презентация

```bash
cd docs/max-presentation
python stamp-commit.py
python build-pdf.py
MAX_BOT_TOKEN=... python build-pdf.py --jury   # не коммитить
```

## Структура

```text
backend/   FastAPI + MAX
frontend/  Next.js + /max
admin/     Django admin
infra/     Traefik, сертификаты
tests/     pytest
docs/      презентация
```
