#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

NOTIFY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/scripts/notify_deploy.sh"
DID_DEPLOY=0

notify_deploy() {
  local phase="$1"
  local detail="${2:-}"
  if [[ ! -f "$NOTIFY_SCRIPT" ]]; then
    echo "WARN notify_deploy: script not found — skip $phase"
    return 0
  fi
  if ! bash "$NOTIFY_SCRIPT" "$phase" "$detail"; then
    echo "WARN notify_deploy $phase failed (ignored)"
  fi
}

on_exit() {
  local code=$?
  if [[ "$DID_DEPLOY" -eq 0 ]]; then
    return
  fi
  if [[ "$code" -eq 0 ]]; then
    notify_deploy finished "deploy.sh exit 0"
  else
    notify_deploy failed "deploy.sh exit ${code}"
  fi
}

trap on_exit EXIT

notify_deploy started "git pull + compose rebuild"
DID_DEPLOY=1

echo "==> Pull from git"
git pull --ff-only origin main

OPENAI_API_KEY="$(grep -E '^OPENAI_API_KEY=' .env | sed -E 's/^OPENAI_API_KEY=//' | tr -d '\"' || true)"
if [ -z "${OPENAI_API_KEY}" ]; then
  echo "WARN: OPENAI_API_KEY is empty in .env; chat/deploy may fail with 'OpenAI key is not configured'."
fi

echo "==> Build + restart stack"
docker compose -f docker-compose.prod.yml --env-file .env up -d --build --remove-orphans

echo "==> Run backend migrations (alembic)"
docker compose -f docker-compose.prod.yml --env-file .env exec -T backend alembic upgrade head

echo "==> Run django-admin migrations"
docker compose -f docker-compose.prod.yml --env-file .env exec -T django-admin python manage.py migrate --noinput
docker compose -f docker-compose.prod.yml --env-file .env exec -T django-admin python manage.py ensure_superuser

echo "==> Seed default SystemSetting (idempotent)"
docker compose -f docker-compose.prod.yml --env-file .env exec -T django-admin python manage.py shell -c "$(cat <<'PY'
from core.models import SystemSetting

def upsert(key: str, **fields) -> None:
    obj, _created = SystemSetting.objects.get_or_create(key=key, defaults=fields)
    for k, v in fields.items():
        setattr(obj, k, v)
    obj.save()

upsert(
    "openai_api_key",
    title="OpenAI API ключ",
    setting_type="api_key",
    value_text="",
    is_enabled=True,
    description="Ключ для OpenAI (не заполнять, если используешь другой провайдер).",
)
upsert(
    "max_projects_per_user",
    title="Лимит созданных проектов на пользователя",
    setting_type="limit",
    value_number=50,
    is_enabled=True,
    description=(
        "Максимум проектов, которые может СОЗДАТЬ один пользователь за всё время "
        "(включая остановленные). Это НЕ лимит одновременно ЗАПУЩЕННЫХ проектов - тот "
        "определяется тарифом пользователя (см. раздел «Тарифы»)."
    ),
)
upsert(
    "enable_chat_files",
    title="Включить файлы в чате",
    setting_type="feature_toggle",
    value_json={"enabled": True},
    is_enabled=True,
    description="Разрешает прикрепление файлов к сообщениям в чате.",
)
upsert(
    "worker_periodic_cleanup",
    title="Периодическая очистка",
    setting_type="periodic_task",
    cron_expression="0 3 * * *",
    is_enabled=False,
    description="Ежедневная задача для очистки временных данных.",
)
print("SystemSetting defaults upserted")
PY
)"

echo "==> Clean up dangling images from superseded builds"
docker image prune -f || true

echo "==> Status"
docker compose -f docker-compose.prod.yml --env-file .env ps

echo "==> Health checks"
curl -sk https://api.airuntime.ru/health || true
curl -skI https://admin.airuntime.ru/ || true
curl -skI https://airuntime.ru/ || true

echo "Done."

