/**
 * Реквизиты оператора сервиса AIRuntime (Исполнитель / Оператор ПДн).
 */
export const operator = {
  brand: "AIRuntime",
  siteUrl: "https://airuntime.ru",
  fullName: "Дымников Михаил Александрович",
  /** Плательщик НПД (самозанятый) — ОГРНИП не применяется */
  status: "self_employed" as const,
  statusLabel: "плательщик налога на профессиональный доход (самозанятый)",
  inn: "780162541444",
  phone: "+7 931 339-38-45",
  email: "mialdym@gmail.com",
  address: "Россия, Санкт-Петербург, 3-я линия В.О., д. 8, кв. 4",
  publishedAt: "2026-09-09",
} as const;

export function operatorDisplayName(): string {
  return operator.fullName;
}
