"use client";

/**
 * Switch between the owner's storefronts the way Clino switches apartments: a tappable
 * current-item card, then a snap wheel in a sheet. Listing every service as a stack of
 * cards made two businesses look like one long feed.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { OwnerService } from "@/lib/max/api";

const ITEM_H = 64;

type Props = {
  open: boolean;
  services: OwnerService[];
  selectedSlug: string;
  onSelect: (slug: string) => void;
  onClose: () => void;
  onAdd: () => void;
};

export function OwnerServicePicker({
  open,
  services,
  selectedSlug,
  onSelect,
  onClose,
  onAdd,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="max-picker-scrim" onClick={onClose}>
      <div
        className="max-picker-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="max-picker-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="max-picker-head">
          <div>
            <p id="max-picker-title" className="max-picker-title">
              Сервис
            </p>
            <p className="max-hint" style={{ marginTop: 4 }}>
              {services.length > 1
                ? "Прокрутите список, чтобы переключить"
                : "Можно держать несколько страниц записи"}
            </p>
          </div>
          <button type="button" className="max-picker-close" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>

        {services.length > 0 ? (
          <ServiceWheel services={services} value={selectedSlug} onChange={onSelect} />
        ) : null}

        <button type="button" className="max-button" style={{ marginTop: 16 }} onClick={onClose}>
          Готово
        </button>
        <button type="button" className="max-button max-button-secondary" style={{ marginTop: 8 }} onClick={onAdd}>
          Добавить ещё
        </button>
      </div>
    </div>
  );
}

function ServiceWheel({
  services,
  value,
  onChange,
}: {
  services: OwnerService[];
  value: string;
  onChange: (slug: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const skipScroll = useRef(false);
  const [openedOn] = useState(() => value);

  const selectedIndex = Math.max(
    0,
    services.findIndex((service) => service.slug === value)
  );
  const selected = services[selectedIndex];

  const padWheel = useCallback(() => {
    const scroller = scrollRef.current;
    const inner = innerRef.current;
    if (!scroller || !inner) return;
    const pad = Math.max(0, (scroller.clientHeight - ITEM_H) / 2);
    inner.style.paddingTop = `${pad}px`;
    inner.style.paddingBottom = `${pad}px`;
  }, []);

  const syncFromScroll = useCallback(() => {
    const root = scrollRef.current;
    if (!root) return;
    const mid = root.getBoundingClientRect().top + root.clientHeight / 2;
    let best = 0;
    let bestDist = Infinity;
    root.querySelectorAll<HTMLElement>("[data-service-index]").forEach((node) => {
      const box = node.getBoundingClientRect();
      const dist = Math.abs(box.top + box.height / 2 - mid);
      if (dist < bestDist) {
        bestDist = dist;
        best = Number(node.dataset.serviceIndex);
      }
    });
    const service = services[best];
    if (service && service.slug !== value) onChange(service.slug);
  }, [onChange, services, value]);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    padWheel();
    const observer = new ResizeObserver(padWheel);
    observer.observe(scroller);
    const start = Math.max(
      0,
      services.findIndex((service) => service.slug === openedOn)
    );
    requestAnimationFrame(() => {
      const el = innerRef.current?.querySelector<HTMLElement>(`[data-service-index="${start}"]`);
      if (!el) return;
      skipScroll.current = true;
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      requestAnimationFrame(() => {
        skipScroll.current = false;
      });
    });
    return () => observer.disconnect();
  }, [openedOn, padWheel, services]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      if (skipScroll.current) return;
      clearTimeout(timer);
      timer = setTimeout(syncFromScroll, 64);
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      root.removeEventListener("scroll", onScroll);
    };
  }, [syncFromScroll]);

  return (
    <div>
      {selected ? (
        <p className="max-picker-caption">
          {KIND_LABEL[selected.config.kind] ?? "Сервис"}
          {selected.new_leads > 0 ? ` · ${selected.new_leads} новых` : ""}
        </p>
      ) : null}
      <div className="max-wheel-wrap">
        <div className="max-wheel-window" aria-hidden />
        <div ref={scrollRef} className="max-wheel">
          <div ref={innerRef}>
            {services.map((service, index) => {
              const active = index === selectedIndex;
              return (
                <button
                  key={service.slug}
                  type="button"
                  data-service-index={index}
                  className={`max-wheel-item${active ? " is-active" : ""}`}
                  onClick={() => {
                    onChange(service.slug);
                    const node = innerRef.current?.querySelector<HTMLElement>(
                      `[data-service-index="${index}"]`
                    );
                    if (!node) return;
                    skipScroll.current = true;
                    node.scrollIntoView({ block: "center", behavior: "smooth" });
                    window.setTimeout(() => {
                      skipScroll.current = false;
                    }, 360);
                  }}
                >
                  <span className="max-wheel-name">{service.config.title}</span>
                  <span className="max-wheel-meta">
                    {service.status === "live" ? "опубликован" : "скрыт"}
                    {service.new_leads > 0 ? ` · ${service.new_leads} новых` : ""}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  booking: "Запись",
  menu: "Меню",
  landing: "Заявки",
};
