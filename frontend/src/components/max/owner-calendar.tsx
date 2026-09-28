"use client";

import { useMemo, useState } from "react";

import { decorateDays, leadsOnDay, upcomingDays, type CalendarDay } from "@/lib/max/calendar";
import type { Lead } from "@/lib/max/api";

type Props = {
  leads: Lead[];
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
};

export function OwnerCalendar({ leads, selectedKey, onSelect }: Props) {
  const [origin] = useState(() => new Date());
  const days = useMemo(() => decorateDays(upcomingDays(origin, 14), leads), [leads, origin]);

  return (
    <div className="max-cal" role="tablist" aria-label="Календарь заявок">
      {days.map((day) => (
        <DayButton
          key={day.key}
          day={day}
          pressed={selectedKey === day.key}
          onSelect={() => onSelect(selectedKey === day.key ? null : day.key)}
        />
      ))}
    </div>
  );
}

function DayButton({
  day,
  pressed,
  onSelect,
}: {
  day: CalendarDay;
  pressed: boolean;
  onSelect: () => void;
}) {
  const today = day.date.toDateString() === new Date().toDateString();
  return (
    <button
      type="button"
      className="max-cal-day"
      role="tab"
      aria-pressed={pressed}
      aria-current={today ? "date" : undefined}
      onClick={onSelect}
    >
      <span className="max-cal-weekday">{day.weekday}</span>
      <span className="max-cal-num">{day.label}</span>
      {day.count > 0 ? <span className="max-cal-count">{day.count}</span> : <span className="max-cal-count max-cal-count-empty" />}
    </button>
  );
}

export function filterLeadsForDay(leads: Lead[], selectedKey: string | null): Lead[] {
  return selectedKey ? leadsOnDay(leads, selectedKey) : leads;
}
