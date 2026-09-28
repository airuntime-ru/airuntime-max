# AIRuntime ops Telegram bot

Сервис `ops-bot` в `docker-compose.prod.yml`. Работает в сети `airuntime_internal`,
читает `DATABASE_URL` из того же `.env`, что и API.

Проверка деплой-нотификаций: `./deploy.sh` шлёт в тему «код» сообщения
«релиз начался» / «релиз завершён» / «релиз упал» через `scripts/notify_deploy.sh`.

## Возможности

### Тема «состояние кода» (`AIRUNTIME_TELEGRAM_TOPIC_CODE`)
- Релиз: начался / завершён / упал (`scripts/notify_deploy.sh` из `deploy.sh`)
- Алерт при многих ERROR/Exception в логах контейнеров (~каждые 10 мин)
- Алерт при падении HTTP health-check (`HEALTH_CHECK_URLS`)
- Алерт при зависших orchestration runs (без обновления &gt; 2 ч)
- Ежедневный отчёт баланса RouterAI вечером (MSK, default 20:00)
- Одноразовый алерт, если баланс RouterAI &lt; 100 ₽ (повтор только после пополнения)

## Настройка

1. BotFather → токен, бот в группу (админ).
2. Узнать `chat_id` и `message_thread_id` тем:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq .
```

3. В `.env` на сервере:

```env
AIRUNTIME_TELEGRAM_BOT_TOKEN=...
AIRUNTIME_TELEGRAM_CHAT_ID=-1004484733656
AIRUNTIME_TELEGRAM_TOPIC_CODE=3
MONITOR_CONTAINERS=airuntime-backend-1,airuntime-worker-1,airuntime-django-admin-1,airuntime-frontend-1
HEALTH_CHECK_URLS=https://api.airuntime.ru/health,https://admin.airuntime.ru/,https://airuntime.ru/
```

4. Поднять вместе со стеком:

```bash
docker compose -f docker-compose.prod.yml up -d --build ops-bot
```

Без токена контейнер не падает — уходит в idle (`sleep infinity`).

## Переменные

| Переменная | Описание |
|------------|----------|
| `AIRUNTIME_TELEGRAM_BOT_TOKEN` | Токен бота |
| `AIRUNTIME_TELEGRAM_CHAT_ID` | ID группы |
| `AIRUNTIME_TELEGRAM_TOPIC_CODE` | ID темы для infra-алертов |
| `ERROR_ALERT_THRESHOLD` | Порог ERROR в логах (default 20) |
| `LOG_CHECK_INTERVAL_SEC` | Интервал проверки логов (default 600) |
| `HEALTH_CHECK_URLS` | CSV URL для HTTP-проверок |
| `MONITOR_CONTAINERS` | CSV имён Docker-контейнеров |
| `DATABASE_URL` | Postgres для stuck-run мониторинга |
| `ROUTERAI_API_KEY` / `OPENAI_API_KEY` | Ключ RouterAI для проверки баланса (пусто = выключено) |
| `OPENAI_BASE_URL` | Base URL RouterAI (default `https://routerai.ru/api/v1`) |
| `ROUTERAI_BALANCE_REPORT_HOUR` | Час ежедневного отчёта по MSK (default 20) |
| `ROUTERAI_LOW_BALANCE_RUB` | Порог одноразового алерта в ₽ (default 100) |
| `ROUTERAI_BALANCE_CHECK_INTERVAL_SEC` | Интервал опроса баланса (default 3600) |
