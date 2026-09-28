import type { Lead } from "@/lib/max/api";

export type CalendarDay = {
  key: string;
  date: Date;
  label: string;
  weekday: string;
  count: number;
};

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function dayKey(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function leadWhen(lead: Pick<Lead, "scheduled_at" | "created_at">): Date | null {
  const raw = lead.scheduled_at || lead.created_at;
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function upcomingDays(from: Date, count = 14): CalendarDay[] {
  const origin = startOfDay(from);
  const mondayOffset = (origin.getDay() + 6) % 7;
  origin.setDate(origin.getDate() - mondayOffset);
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(origin);
    date.setDate(origin.getDate() + index);
    return {
      key: dayKey(date),
      date,
      label: String(date.getDate()),
      weekday: date.toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", ""),
      count: 0,
    };
  });
}

export function decorateDays(days: CalendarDay[], leads: Lead[]): CalendarDay[] {
  const counts = new Map<string, number>();
  for (const lead of leads) {
    const when = leadWhen(lead);
    if (!when) continue;
    const key = dayKey(when);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return days.map((day) => ({ ...day, count: counts.get(day.key) || 0 }));
}

export function leadsOnDay(leads: Lead[], key: string | null): Lead[] {
  if (!key) return leads;
  return leads.filter((lead) => {
    const when = leadWhen(lead);
    return when ? dayKey(when) === key : false;
  });
}
