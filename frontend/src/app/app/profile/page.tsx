"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  Layers,
  UserRound,
} from "lucide-react";

import { ByokSection } from "@/components/app/byok-section";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageLoader } from "@/components/ui/loader";
import { Modal } from "@/components/ui/modal";
import {
  type BillingSummaryType,
  type CreditLedgerEntryType,
  type CreditTopUpType,
  type LedgerDirection,
  type PlanType,
  createTopUp,
  getBillingSummary,
  getUsageHistory,
  listPlans,
  listTopUps,
  requestPlanChange,
  cancelPlanRequest,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { useProfile } from "@/lib/use-profile";

const TOPUP_PRESETS = [10_000, 50_000, 200_000];
const LEDGER_PAGE_SIZE = 10;

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const LEDGER_REASON_LABEL: Record<CreditLedgerEntryType["reason"], string> = {
  chat_message: "Сообщение в чате",
  topup: "Пополнение баланса",
  period_renewal: "Обновление тарифного периода",
  plan_change: "Смена тарифа",
  signup_grant: "Стартовый бюджет",
};

const LEDGER_DIRECTION_OPTIONS: { value: LedgerDirection; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "debit", label: "Списания" },
  { value: "credit", label: "Начисления" },
];

function ledgerTitle(entry: CreditLedgerEntryType): string {
  if (entry.project_name) return entry.project_name;
  return LEDGER_REASON_LABEL[entry.reason] ?? entry.reason;
}

function ledgerSubtitle(entry: CreditLedgerEntryType): string {
  const reason = LEDGER_REASON_LABEL[entry.reason] ?? entry.reason;
  const model = entry.model ? ` · ${entry.model}` : "";
  if (entry.project_name && entry.reason === "chat_message") {
    return `${reason}${model} · ${formatDateTime(entry.created_at)}`;
  }
  return formatDateTime(entry.created_at);
}

function formatRub(value: number): string {
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// "до N проектов": genitive case throughout, so only the "N=1" form differs ("до 1 проекта" vs "до 5 проектов").
function pluralizeProjects(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  return mod10 === 1 && mod100 !== 11 ? "проекта" : "проектов";
}

const TOPUP_STATUS_LABEL: Record<CreditTopUpType["status"], string> = {
  pending: "Ожидает оплаты",
  paid: "Оплачен",
  cancelled: "Отменён",
};

const TOPUP_STATUS_TONE: Record<CreditTopUpType["status"], string> = {
  pending: "border-amber-500/30 bg-amber-50 text-amber-800",
  paid: "border-emerald-500/25 bg-emerald-50 text-emerald-700",
  cancelled: "border-black/10 bg-black/[0.03] text-[var(--ar-mist)]",
};

export default function ProfilePage() {
  const { profile, error, loading } = useProfile();
  const [billing, setBilling] = useState<BillingSummaryType | null>(null);
  const [topups, setTopups] = useState<CreditTopUpType[]>([]);
  const [usage, setUsage] = useState<CreditLedgerEntryType[]>([]);
  const [usageTotal, setUsageTotal] = useState(0);
  const [usagePage, setUsagePage] = useState(0);
  const [usageDirection, setUsageDirection] = useState<LedgerDirection>("all");
  const [usageLoading, setUsageLoading] = useState(false);
  const [plans, setPlans] = useState<PlanType[]>([]);
  const [topupOpen, setTopupOpen] = useState(false);
  const [topupCredits, setTopupCredits] = useState(TOPUP_PRESETS[0]);
  const [topupBusy, setTopupBusy] = useState(false);
  const [topupError, setTopupError] = useState<string | null>(null);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [confirmPlan, setConfirmPlan] = useState<PlanType | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const payment = searchParams.get("payment");
  const paymentNotice = payment === "success" || payment === "fail" ? payment : null;

  const loadUsage = useCallback(async (page: number, direction: LedgerDirection) => {
    setUsageLoading(true);
    try {
      const usagePageResult = await getUsageHistory(LEDGER_PAGE_SIZE, page * LEDGER_PAGE_SIZE, direction);
      setUsage(usagePageResult.items);
      setUsageTotal(usagePageResult.total);
    } finally {
      setUsageLoading(false);
    }
  }, []);

  const loadBilling = useCallback(async () => {
    const [summary, invoices, planRows] = await Promise.all([
      getBillingSummary(),
      listTopUps(),
      listPlans(),
    ]);
    setBilling(summary);
    setTopups(invoices);
    setPlans(planRows);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBilling();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadBilling]);

  useEffect(() => {
    if (paymentNotice !== "success") return;
    let tries = 0;
    const poll = window.setInterval(() => {
      tries += 1;
      void loadBilling();
      if (tries >= 6) window.clearInterval(poll);
    }, 2000);
    return () => window.clearInterval(poll);
  }, [paymentNotice, loadBilling]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadUsage(usagePage, usageDirection);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [usagePage, usageDirection, loadUsage]);

  const usagePageCount = Math.max(1, Math.ceil(usageTotal / LEDGER_PAGE_SIZE));

  const onDirectionChange = (direction: LedgerDirection) => {
    setUsageDirection(direction);
    setUsagePage(0);
  };

  const onRequestTopup = async () => {
    setTopupBusy(true);
    setTopupError(null);
    try {
      const invoice = await createTopUp(topupCredits);
      if (invoice.payment_url) {
        window.location.assign(invoice.payment_url);
        return;
      }
      setUsagePage(0);
      await loadBilling();
      await loadUsage(0, usageDirection);
      setTopupOpen(false);
    } catch (err) {
      setTopupError(err instanceof Error ? err.message : "Не удалось создать счёт");
    } finally {
      setTopupBusy(false);
    }
  };

  const onConfirmPlanRequest = async () => {
    if (!confirmPlan) return;
    setPlanBusy(true);
    setPlanError(null);
    try {
      await requestPlanChange(confirmPlan.id);
      await loadBilling();
      setConfirmPlan(null);
      setPlanModalOpen(false);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Не удалось отправить заявку");
    } finally {
      setPlanBusy(false);
    }
  };

  const onCancelPlanRequest = async () => {
    const pending = billing?.pending_plan_request;
    if (!pending) return;
    setPlanBusy(true);
    try {
      await cancelPlanRequest(pending.id);
      await loadBilling();
    } finally {
      setPlanBusy(false);
    }
  };

  const rubForCredits = (credits: number) => Math.max(1, Math.ceil((credits * 10) / 1000));

  if (loading) return <PageLoader />;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-[-0.025em] text-[var(--ar-black)] sm:text-[1.75rem]">
          Профиль
        </h1>
        <p className="mt-1 text-sm text-[var(--ar-mist)]">Аккаунт, тариф и расход кредитов</p>
      </header>

      {error ? <p className="text-sm text-rose-600">{error}</p> : null}

      {paymentNotice === "success" ? (
        <p className="rounded-[0.7rem] border border-emerald-500/25 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          Оплата прошла. Кредиты появятся на балансе в течение нескольких секунд.
        </p>
      ) : null}
      {paymentNotice === "fail" ? (
        <p className="rounded-[0.7rem] border border-rose-500/25 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          Оплата не завершена. Можно попробовать ещё раз — счёт останется в списке, пока его не оплатите.
        </p>
      ) : null}

      {profile ? (
        <Card hover={false} className="flex flex-wrap items-center gap-x-4 gap-y-3 p-5">
          <span
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]"
            aria-hidden
          >
            <UserRound size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-[var(--ar-black)]">{profile.email}</p>
            <p
              className={cn(
                "mt-0.5 inline-flex items-center gap-1.5 text-sm",
                profile.is_verified ? "text-emerald-600" : "text-[var(--ar-stone)]"
              )}
            >
              <CheckCircle2 size={14} aria-hidden />
              {profile.is_verified ? "Почта подтверждена" : "Почта не подтверждена"}
            </p>
          </div>
        </Card>
      ) : null}

      {billing ? (
        <>
          <Card hover={false} className="p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-5">
              <div>
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                  Баланс
                </p>
                {/* Rubles are the unit users think in; credits are an internal detail. */}
                <p className="mt-2 text-[2.25rem] font-semibold leading-none tabular-nums tracking-[-0.03em] text-[var(--ar-black)]">
                  {formatRub(billing.balance_rub)}
                  <span className="ml-2 text-base font-medium text-[var(--ar-stone)]">₽</span>
                </p>
                <p className="mt-1 text-xs tabular-nums text-[var(--ar-stone)]">
                  {billing.credits_balance.toLocaleString("ru-RU")} кредитов
                </p>
                <p className="mt-3 text-sm leading-relaxed text-[var(--ar-mist)]">
                  Тариф «{billing.plan?.name ?? "не назначен"}»
                  {billing.plan
                    ? ` · ${billing.plan.monthly_budget_rub} ₽ ${billing.plan.grant_renews ? "в месяц" : "разово при регистрации"} · до ${billing.plan.max_concurrent_projects} ${pluralizeProjects(billing.plan.max_concurrent_projects)} одновременно`
                    : " — обратитесь в поддержку, чтобы подключить тариф"}
                </p>
              </div>
              <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
                <Button
                  variant="outline"
                  onClick={() => setPlanModalOpen(true)}
                  disabled={Boolean(billing.pending_plan_request)}
                >
                  <Layers size={15} />
                  {billing.pending_plan_request ? "Заявка отправлена" : "Сменить тариф"}
                </Button>
                <Button variant="accent" onClick={() => setTopupOpen(true)}>
                  <CreditCard size={15} />
                  Пополнить
                </Button>
              </div>
            </div>

            {billing.pending_plan_request ? (
              <div className="mt-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-[0.7rem] border border-amber-500/30 bg-amber-50 px-4 py-3">
                <p className="text-sm text-amber-900">
                  Заявка на тариф «{billing.pending_plan_request.to_plan_name}» на рассмотрении.
                  Подключим после подтверждения оплаты.
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={planBusy}
                  onClick={() => void onCancelPlanRequest()}
                >
                  Отменить заявку
                </Button>
              </div>
            ) : null}

            <dl className="mt-6 grid gap-x-6 gap-y-3 border-t border-black/[0.06] pt-4 sm:grid-cols-2">
              <div className="flex items-baseline justify-between gap-3 sm:justify-start sm:gap-2">
                <dt className="text-sm text-[var(--ar-stone)]">Период начался</dt>
                <dd className="text-sm font-medium text-[var(--ar-black)]">
                  {formatDate(billing.billing_period_start)}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3 sm:justify-start sm:gap-2">
                <dt className="text-sm text-[var(--ar-stone)]">Обновление тарифа</dt>
                <dd className="text-sm font-medium text-[var(--ar-black)]">
                  {formatDate(billing.billing_period_end)}
                </dd>
              </div>
            </dl>
          </Card>

          <ByokSection />

          {topups.length > 0 ? (
            <Card hover={false} className="p-5 sm:p-6">
              <h2 className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                Счета на пополнение
              </h2>
              <ul className="mt-3 space-y-2">
                {topups.map((invoice) => (
                  <li
                    key={invoice.id}
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 rounded-[0.7rem] border border-black/[0.06] px-3.5 py-2.5 text-sm"
                  >
                    <span className="font-medium text-[var(--ar-black)]">
                      {invoice.credits.toLocaleString("ru-RU")} кредитов · {invoice.amount_rub} ₽
                    </span>
                    <span className="flex items-center gap-2">
                      {invoice.status === "pending" && invoice.payment_url ? (
                        <a
                          href={invoice.payment_url}
                          className="text-xs font-semibold text-[var(--ar-sky)] hover:underline"
                        >
                          Оплатить
                        </a>
                      ) : null}
                      <span
                        className={cn(
                          "rounded-full border px-2.5 py-1 text-xs font-semibold",
                          TOPUP_STATUS_TONE[invoice.status]
                        )}
                      >
                        {TOPUP_STATUS_LABEL[invoice.status]}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card hover={false} className="p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
              <h2 className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                История кредитов
              </h2>
              <div className="flex flex-wrap gap-1.5">
                {LEDGER_DIRECTION_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => onDirectionChange(option.value)}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                      usageDirection === option.value
                        ? "border-transparent bg-[var(--ar-black)] text-white"
                        : "border-black/10 text-[var(--ar-mist)] hover:border-black/20 hover:text-[var(--ar-black)]"
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            {usage.length > 0 ? (
              <ul
                className={cn(
                  "mt-4 divide-y divide-black/[0.06] transition-opacity",
                  usageLoading && "opacity-50"
                )}
              >
                {usage.map((entry) => (
                  <li
                    key={entry.id}
                    // Wraps instead of truncating: on a 390px screen the project name and the
                    // amount each get a full line rather than fighting for one.
                    className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-3"
                  >
                    <div className="min-w-0 flex-1 basis-[11rem]">
                      <p className="text-sm font-medium text-[var(--ar-black)]">
                        {ledgerTitle(entry)}
                      </p>
                      <p className="mt-0.5 text-xs leading-relaxed text-[var(--ar-stone)]">
                        {ledgerSubtitle(entry)}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p
                        className={cn(
                          "whitespace-nowrap text-sm font-semibold tabular-nums",
                          entry.amount >= 0 ? "text-emerald-600" : "text-[var(--ar-black)]"
                        )}
                      >
                        {entry.amount >= 0 ? "+" : ""}
                        {entry.amount.toLocaleString("ru-RU")} кредитов
                      </p>
                      {entry.cost_rub !== null ? (
                        <p className="mt-0.5 whitespace-nowrap text-xs tabular-nums text-[var(--ar-stone)]">
                          {formatRub(entry.cost_rub)} ₽
                        </p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-4 text-sm text-[var(--ar-mist)]">
                {usageLoading ? "Загружаем историю…" : "Пока нет записей по выбранному фильтру"}
              </p>
            )}

            {usageTotal > LEDGER_PAGE_SIZE ? (
              <div className="mt-4 flex items-center justify-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={usagePage <= 0 || usageLoading}
                  onClick={() => setUsagePage((page) => Math.max(0, page - 1))}
                >
                  <ChevronLeft size={15} />
                  Назад
                </Button>
                <span className="text-xs tabular-nums text-[var(--ar-stone)]">
                  {usagePage + 1} / {usagePageCount}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={usagePage >= usagePageCount - 1 || usageLoading}
                  onClick={() => setUsagePage((page) => Math.min(usagePageCount - 1, page + 1))}
                >
                  Вперёд
                  <ChevronRight size={15} />
                </Button>
              </div>
            ) : null}
          </Card>
        </>
      ) : null}

      <Modal
        open={topupOpen}
        onClose={() => setTopupOpen(false)}
        title="Пополнить баланс"
        description={
          billing?.robokassa_enabled
            ? "Выберите объём кредитов. Откроется оплата через Robokassa — после успешного платежа кредиты зачислятся автоматически."
            : "Выберите объём кредитов. После создания счёта администратор подтвердит оплату вручную и кредиты зачислятся автоматически."
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-2">
            {TOPUP_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setTopupCredits(preset)}
                className={cn(
                  "rounded-[0.7rem] border px-3 py-2.5 text-sm font-medium transition-colors",
                  topupCredits === preset
                    ? "border-[var(--ar-sky)] bg-[var(--ar-sky)]/[0.08] text-[var(--ar-sky)]"
                    : "border-black/10 text-[var(--ar-black)] hover:border-black/20"
                )}
              >
                {preset.toLocaleString("ru-RU")}
              </button>
            ))}
          </div>
          <p className="text-sm text-[var(--ar-mist)]">
            Стоимость:{" "}
            <span className="font-semibold text-[var(--ar-black)]">
              {rubForCredits(topupCredits)} ₽
            </span>
          </p>
          {topupError ? <p className="text-sm text-rose-600">{topupError}</p> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="ghost" onClick={() => setTopupOpen(false)} disabled={topupBusy}>
              Отмена
            </Button>
            <Button variant="accent" onClick={() => void onRequestTopup()} disabled={topupBusy}>
              {topupBusy
                ? billing?.robokassa_enabled
                  ? "Переходим к оплате…"
                  : "Создаём счёт…"
                : billing?.robokassa_enabled
                  ? "Оплатить"
                  : "Создать счёт"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={planModalOpen}
        onClose={() => setPlanModalOpen(false)}
        title="Выберите тариф"
        description="Тариф подключается после подтверждения оплаты — заявка уйдёт на рассмотрение."
      >
        <div className="space-y-2">
          {plans.map((plan) => {
            const isCurrent = billing?.plan?.id === plan.id;
            return (
              <button
                key={plan.id}
                type="button"
                disabled={isCurrent}
                onClick={() => setConfirmPlan(plan)}
                className={cn(
                  "w-full rounded-[0.7rem] border p-3.5 text-left transition-colors",
                  isCurrent
                    ? "cursor-default border-[var(--ar-sky)]/40 bg-[var(--ar-sky)]/[0.05]"
                    : "border-black/10 hover:border-black/20"
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-semibold text-[var(--ar-black)]">
                    {plan.name}
                    {isCurrent ? (
                      <span className="ml-2 text-xs font-normal text-[var(--ar-sky)]">текущий</span>
                    ) : null}
                  </p>
                  <p className="font-semibold tabular-nums text-[var(--ar-black)]">
                    {plan.price_rub > 0 ? `${plan.price_rub} ₽/мес` : "Бесплатно"}
                  </p>
                </div>
                <p className="mt-1 text-sm text-[var(--ar-mist)]">
                  {plan.monthly_budget_rub} ₽ на модели{" "}
                  {plan.grant_renews ? "каждый месяц" : "разово при регистрации"} · до{" "}
                  {plan.max_projects} {pluralizeProjects(plan.max_projects)} · до{" "}
                  {plan.max_concurrent_projects} запущено одновременно
                </p>
                {plan.allowed_models ? (
                  <p className="mt-1 text-xs text-[var(--ar-stone)]">
                    Модели: только экономичная. Sol и Terra — на платных тарифах.
                  </p>
                ) : null}
              </button>
            );
          })}
        </div>
      </Modal>

      <Modal
        open={Boolean(confirmPlan)}
        onClose={() => setConfirmPlan(null)}
        title="Подать заявку на тариф?"
        description={
          confirmPlan
            ? `Отправим заявку на «${confirmPlan.name}» (${confirmPlan.price_rub} ₽/мес). Мы свяжемся по оплате — тариф и бюджет ${confirmPlan.monthly_budget_rub} ₽ подключатся после подтверждения.`
            : undefined
        }
      >
        <div className="space-y-4">
          {planError ? <p className="text-sm text-rose-600">{planError}</p> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="ghost" onClick={() => setConfirmPlan(null)} disabled={planBusy}>
              Отмена
            </Button>
            <Button
              variant="accent"
              onClick={() => void onConfirmPlanRequest()}
              disabled={planBusy}
            >
              {planBusy ? "Отправляем…" : "Отправить заявку"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
