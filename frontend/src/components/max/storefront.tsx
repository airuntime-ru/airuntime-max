"use client";

/**
 * The customer's whole journey: open the deep link, pick, book, done.
 *
 * Three rules shaped this screen, all from how a messenger mini app is actually used:
 * - one screen, no navigation. A booking that spans steps loses people in a webview.
 * - the phone number comes from MAX itself when the client offers it (`requestContact`),
 *   so the common case is zero typing.
 * - every state is visible: loading, empty, error, submitting, done. A silent failure in
 *   a webview reads as a broken business, not a broken app.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  MaxApiError,
  createLead,
  fetchService,
  type CustomerLead,
  type ServiceConfig,
  type ServiceItem,
} from "@/lib/max/api";
import { getInitDataUnsafe, requestContact } from "@/lib/max/bridge";
import { commentHint, kindKicker, storefrontDesign } from "@/lib/max/theme";

type Phase = "loading" | "ready" | "submitting" | "done" | "error";

function priceLabel(item: ServiceItem): string {
  if (item.price_rub === null || item.price_rub === undefined) return "";
  if (item.price_rub === 0) return "Бесплатно";
  return `${item.price_rub.toLocaleString("ru-RU")} ₽`;
}

function historyStatus(status: CustomerLead["status"]): string {
  if (status === "confirmed") return "подтверждена";
  if (status === "declined") return "отклонена";
  if (status === "done") return "выполнена";
  return "на рассмотрении";
}

function HistoryList({ leads }: { leads: CustomerLead[] }) {
  if (leads.length === 0) return null;
  return (
    <section className="max-history">
      <h2 className="max-section-title">Ваши записи</h2>
      <ul className="max-list">
        {leads.map((lead) => (
          <li key={lead.id} className="max-sheet max-history-row">
            <div className="max-lead-head">
              <span className="max-option-title">{lead.item_title || "Заявка"}</span>
              <span
                className={`max-badge ${lead.status === "confirmed" || lead.status === "done" ? "max-badge-confirmed" : lead.status === "declined" ? "max-badge-declined" : "max-badge-new"}`}
              >
                {historyStatus(lead.status)}
              </span>
            </div>
            {lead.slot_label ? <p className="max-option-note">{lead.slot_label}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function durationLabel(item: ServiceItem): string {
  if (!item.duration_min) return "";
  return item.duration_min >= 60 && item.duration_min % 60 === 0
    ? `${item.duration_min / 60} ч`
    : `${item.duration_min} мин`;
}

export function Storefront({ slug }: { slug: string }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [config, setConfig] = useState<ServiceConfig | null>(null);
  const [error, setError] = useState("");

  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [selectedSlot, setSelectedSlot] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [comment, setComment] = useState("");
  const [phoneVerified, setPhoneVerified] = useState(false);
  // Sticks once the customer asks to type a number themselves, so declining the MAX
  // dialog does not bounce them back to a button they already rejected.
  const [phoneManual, setPhoneManual] = useState(false);
  const [successText, setSuccessText] = useState("");
  const [history, setHistory] = useState<CustomerLead[]>([]);
  const [activeCategory, setActiveCategory] = useState("");

  useEffect(() => {
    let cancelled = false;
    // No synchronous setState here: "loading" is already the initial phase, and the slug
    // is fixed for the lifetime of this component (the page mounts it once per launch).
    fetchService(slug)
      .then((response) => {
        if (cancelled) return;
        setConfig(response.config);
        // One offer means there is nothing to choose: open straight on the form.
        if (response.config.items.length === 1) {
          setQuantities({ [response.config.items[0].title]: 1 });
        }
        setHistory(response.my_leads || []);
        // Prefill the name from MAX so most customers only pick a time and tap once.
        const user = getInitDataUnsafe().user;
        const full = [user?.first_name, user?.last_name].filter(Boolean).join(" ").trim();
        if (full) setName(full);
        setPhase("ready");
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        const message =
          cause instanceof MaxApiError && cause.status === 404
            ? "AIRuntime не найден или снят с публикации"
            : cause instanceof Error
              ? cause.message
              : "Не удалось загрузить AIRuntime";
        setError(message);
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const design = useMemo(() => (config ? storefrontDesign(config) : null), [config]);

  const categories = useMemo(
    () =>
      Array.from(
        new Set((config?.items || []).map((item) => item.category?.trim()).filter(Boolean))
      ) as string[],
    [config]
  );

  const effectiveCategory = categories.includes(activeCategory) ? activeCategory : "";

  const selectedEntries = useMemo(
    () =>
      (config?.items || [])
        .map((item) => ({ item, quantity: quantities[item.title] || 0 }))
        .filter((entry) => entry.quantity > 0),
    [config, quantities]
  );
  const selectedItem = selectedEntries[0]?.item.title ?? "";
  const selectedCount = selectedEntries.reduce((sum, entry) => sum + entry.quantity, 0);
  const selectedTotal = selectedEntries.reduce(
    (sum, entry) => sum + (entry.item.price_rub ?? 0) * entry.quantity,
    0
  );
  const hasPricedSelection = selectedEntries.some((entry) => entry.item.price_rub != null);
  const selectionLabel = selectedEntries
    .map(({ item, quantity }) =>
      config?.allow_multiple_items ? `${quantity}× ${item.title}` : item.title
    )
    .join(", ");

  const needsSlot = Boolean(config && config.kind === "booking" && config.slots.length > 0);
  const canSubmit =
    phase === "ready" &&
    Boolean(config) &&
    (config!.items.length === 0 || selectedEntries.length > 0) &&
    (!needsSlot || Boolean(selectedSlot)) &&
    name.trim().length > 0;

  const onShareContact = useCallback(async () => {
    const contact = await requestContact();
    if (!contact) return;
    setPhone(contact.phone);
    // Marked verified only for the customer's own reassurance; the backend re-checks the
    // signature and never takes this flag from the client.
    setPhoneVerified(true);
  }, []);

  const chooseItem = useCallback(
    (title: string) => {
      setQuantities((current) => {
        if (!config?.allow_multiple_items) return { [title]: 1 };
        return current[title] ? current : { ...current, [title]: 1 };
      });
    },
    [config?.allow_multiple_items]
  );

  const changeQuantity = useCallback((title: string, delta: number) => {
    setQuantities((current) => {
      const next = Math.max(0, Math.min(20, (current[title] || 0) + delta));
      if (next === 0) {
        const rest = { ...current };
        delete rest[title];
        return rest;
      }
      return { ...current, [title]: next };
    });
  }, []);

  const onSubmit = useCallback(async () => {
    if (!config || !canSubmit) return;
    setPhase("submitting");
    setError("");
    try {
      const result = await createLead({
        slug,
        item_title: config.allow_multiple_items ? "" : selectedItem,
        items: selectedEntries.map(({ item, quantity }) => ({ title: item.title, quantity })),
        slot_label: selectedSlot,
        customer_name: name.trim(),
        phone: phone.trim(),
        comment: comment.trim(),
      });
      setSuccessText(result.success_message || config.success_message);
      setHistory((current) => [
        {
          id: result.id,
          item_title: result.item_title,
          slot_label: selectedSlot,
          status: "new",
          created_at: new Date().toISOString(),
          scheduled_at: null,
        },
        ...current,
      ]);
      setPhase("done");
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Не удалось отправить заявку");
      setPhase("ready");
    }
  }, [canSubmit, comment, config, name, phone, selectedEntries, selectedItem, selectedSlot, slug]);

  if (phase === "loading") {
    return (
      <div className="max-shell" aria-busy="true" aria-live="polite">
        <div className="max-skeleton" style={{ height: 120 }} />
        <div className="max-skeleton" style={{ height: 18, width: "40%", margin: "24px 0 10px" }} />
        <div className="max-list">
          <div className="max-skeleton" style={{ height: 66 }} />
          <div className="max-skeleton" style={{ height: 66 }} />
          <div className="max-skeleton" style={{ height: 66 }} />
        </div>
        <p className="max-note">Загружаем AIRuntime…</p>
      </div>
    );
  }

  if (phase === "error" || !config) {
    return (
      <div className="max-shell">
        <div className="max-error" role="alert">
          {error || "Не удалось загрузить AIRuntime"}
        </div>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div className="max-shell" {...design?.attrs} style={design?.style}>
        <div className="max-sheet max-success" role="status">
          <div className="max-success-mark" aria-hidden>
            ✓
          </div>
          <h1 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 650 }}>{successText}</h1>
          <p className="max-note">
            {selectionLabel}
            {selectedSlot ? ` · ${selectedSlot}` : ""}
          </p>
          <p className="max-note">Ответ придёт сюда, в MAX.</p>
        </div>
        <HistoryList leads={history} />
      </div>
    );
  }

  const busy = phase === "submitting";
  const visibleItems = effectiveCategory
    ? config.items.filter((item) => item.category === effectiveCategory)
    : config.items;
  const heroImages = Array.from(
    new Set([config.hero_image, ...config.items.map((item) => item.image_url)].filter(Boolean))
  ).slice(0, config.hero_style === "collage" ? 3 : 1);
  const sectionOrder = config.section_order?.length
    ? config.section_order
    : (["hero", "story", "catalog"] as const);
  const sectionRank = (name: "hero" | "story" | "catalog") => {
    const index = sectionOrder.indexOf(name);
    return -3 + (index < 0 ? 3 : index);
  };
  const meta = [config.contacts.address, config.contacts.hours, config.contacts.phone]
    .filter(Boolean)
    .join(" · ");
  const highlights = config.highlights || [];
  // The ticker repeats what is actually on offer - categories when there are several,
  // otherwise the positions themselves - so it never needs copy the owner did not write.
  const tickerWords = config.marquee
    ? categories.length > 2
      ? categories
      : config.items.map((item) => item.title).slice(0, 8)
    : [];
  // Contacts only appear once there is something to book. On a phone, opening straight
  // into three empty fields reads as paperwork rather than as a two-tap booking.
  const showContacts = config.items.length === 0 || selectedEntries.length > 0;
  const missing = selectedEntries.length === 0
    ? config.kind === "menu"
      ? config.allow_multiple_items
        ? "Добавьте позиции"
        : "Выберите позицию"
      : config.allow_multiple_items
        ? "Добавьте услуги"
        : "Выберите услугу"
    : needsSlot && !selectedSlot
      ? "Выберите время"
      : !name.trim()
        ? "Укажите имя"
        : "";
  const withMedia =
    config.card_style === "image-top" ||
    config.card_style === "overlay" ||
    config.card_style === "horizontal";

  return (
    <div className="max-shell" {...design?.attrs} style={design?.style}>
      <header
        className="max-hero"
        data-has-image={heroImages.length > 0 ? "yes" : "no"}
        style={{ order: sectionRank("hero") }}
      >
        {heroImages.length > 0 ? (
          <div className="max-hero-media" aria-hidden="true" data-count={heroImages.length}>
            {heroImages.map((image, index) => (
              // Source images come from the owner's site and keep their original URL.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={image}
                className="max-hero-image"
                src={image}
                alt=""
                data-index={index}
                referrerPolicy="no-referrer"
              />
            ))}
          </div>
        ) : null}
        <div className="max-hero-copy">
          <p className="max-hero-kicker">{config.kicker || kindKicker(config.kind)}</p>
          <h1>{config.title}</h1>
          {config.tagline ? <p className="max-hero-tagline">{config.tagline}</p> : null}
          {highlights.length > 0 ? (
            <dl className="max-highlights" data-count={highlights.length}>
              {highlights.map((fact) => (
                <div key={fact.value + fact.label} className="max-highlight">
                  <dt>{fact.value}</dt>
                  {fact.label ? <dd>{fact.label}</dd> : null}
                </div>
              ))}
            </dl>
          ) : null}
          {meta ? <p className="max-hero-meta">{meta}</p> : null}
        </div>
      </header>

      {tickerWords.length > 0 ? (
        <div className="max-marquee" aria-hidden="true" style={{ order: sectionRank("hero") }}>
          <div className="max-marquee-track">
            {[0, 1].map((copy) => (
              <span key={copy} className="max-marquee-run">
                {tickerWords.map((word) => (
                  <span key={word} className="max-marquee-word">
                    {word}
                  </span>
                ))}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {config.about ? (
        <p className="max-about" style={{ order: sectionRank("story") }}>
          {config.about}
        </p>
      ) : null}

      {history.length > 0 ? <HistoryList leads={history} /> : null}

      {config.items.length > 0 ? (
        <section className="max-catalog-section" style={{ order: sectionRank("catalog") }}>
          <h2 className="max-section-title max-catalog-title">
            {config.catalog_title || (config.kind === "menu" ? "Меню" : "Услуги")}
          </h2>
          {categories.length > 1 && config.nav_style !== "none" ? (
            <nav className="max-category-nav" aria-label="Категории">
              <button
                type="button"
                className="max-category"
                aria-pressed={!effectiveCategory}
                onClick={() => setActiveCategory("")}
              >
                Всё
              </button>
              {categories.map((category) => (
                <button
                  key={category}
                  type="button"
                  className="max-category"
                  aria-pressed={effectiveCategory === category}
                  onClick={() => setActiveCategory(category)}
                >
                  {category}
                </button>
              ))}
            </nav>
          ) : null}
          <ul className="max-list">
            {visibleItems.map((item, index) => {
              const price = priceLabel(item);
              const duration = durationLabel(item);
              const note = item.description;
              const quantity = quantities[item.title] || 0;
              const active = quantity > 0;
              return (
                <li key={item.title} className="max-product-row">
                  <button
                    type="button"
                    className="max-option"
                    aria-pressed={active}
                    data-photo={item.image_url ? "yes" : "no"}
                    onClick={() => chooseItem(item.title)}
                    disabled={busy}
                  >
                    {item.image_url ? (
                      <span className="max-option-media">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={item.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        {item.badge ? <span className="max-product-badge">{item.badge}</span> : null}
                      </span>
                    ) : withMedia ? (
                      // No photo: a tile in the storefront's own colours with the initial,
                      // instead of a grey box that reads as "image failed to load".
                      <span className="max-option-media max-option-tile" aria-hidden="true">
                        <span className="max-option-initial">{item.title.trim().charAt(0)}</span>
                        {item.badge ? <span className="max-product-badge">{item.badge}</span> : null}
                      </span>
                    ) : null}
                    <span className="max-option-index" aria-hidden="true">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    {/* A check mark, not just a border: on a small screen the selected row
                        has to be obvious at a glance. */}
                    <span className="max-radio" aria-hidden>
                      {active ? (config.allow_multiple_items ? quantity : "✓") : ""}
                    </span>
                    <span className="max-option-label">
                      <span className="max-option-title">
                        {item.title}
                        {item.badge && !withMedia ? (
                          <span className="max-inline-badge">{item.badge}</span>
                        ) : null}
                      </span>
                      {note ? <span className="max-option-note">{note}</span> : null}
                    </span>
                    <span className="max-option-meta">
                      {duration ? <span className="max-duration">{duration}</span> : null}
                      {price ? <span className="max-option-price">{price}</span> : null}
                    </span>
                  </button>
                  {config.allow_multiple_items && active ? (
                    <div className="max-quantity" aria-label={`Количество: ${item.title}`}>
                      <button
                        type="button"
                        onClick={() => changeQuantity(item.title, -1)}
                        disabled={busy}
                        aria-label={`Уменьшить количество: ${item.title}`}
                      >
                        −
                      </button>
                      <span aria-live="polite">{quantity}</span>
                      <button
                        type="button"
                        onClick={() => changeQuantity(item.title, 1)}
                        disabled={busy || quantity >= 20}
                        aria-label={`Увеличить количество: ${item.title}`}
                      >
                        +
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {config.allow_multiple_items && selectedEntries.length > 0 ? (
        <section className="max-cart" aria-label="Корзина">
          <div className="max-cart-head">
            <h2 className="max-section-title">Ваш заказ</h2>
            <span>{selectedCount} шт.</span>
          </div>
          <ul>
            {selectedEntries.map(({ item, quantity }) => (
              <li key={item.title}>
                <span>{item.title}</span>
                <span>
                  {quantity} × {priceLabel(item) || "по запросу"}
                </span>
              </li>
            ))}
          </ul>
          {hasPricedSelection ? (
            <div className="max-cart-total">
              <span>Итого</span>
              <strong>{selectedTotal.toLocaleString("ru-RU")} ₽</strong>
            </div>
          ) : null}
        </section>
      ) : null}

      {needsSlot && selectedEntries.length > 0 ? (
        <>
          <h2 className="max-section-title">Когда удобно</h2>
          <div className="max-chips">
            {config.slots.map((slot) => (
              <button
                key={slot}
                type="button"
                className="max-chip"
                aria-pressed={selectedSlot === slot}
                onClick={() => setSelectedSlot(slot)}
                disabled={busy}
              >
                {slot}
              </button>
            ))}
          </div>
        </>
      ) : null}

      {showContacts ? (
        <>
          <h2 className="max-section-title">Контакты</h2>
          <div className="max-sheet max-form">
            <label className="max-field" style={{ marginTop: 0 }}>
              <span>Имя</span>
              <input
                className="max-input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Как к вам обращаться"
                autoComplete="name"
                disabled={busy}
              />
            </label>

            {config.ask_phone ? (
              <div className="max-field">
                <span className="max-field-label">Телефон</span>
                {phone || phoneManual ? (
                  <>
                    <input
                      className="max-input"
                      value={phone}
                      onChange={(event) => {
                        setPhone(event.target.value);
                        setPhoneVerified(false);
                      }}
                      placeholder="+7 900 000-00-00"
                      inputMode="tel"
                      autoComplete="tel"
                      disabled={busy}
                    />
                    {phoneVerified ? (
                      <p className="max-hint max-hint-ok">Номер подтверждён аккаунтом MAX</p>
                    ) : null}
                  </>
                ) : (
                  <>
                    {/* The whole point of being inside MAX: the common case is zero typing. */}
                    <button
                      type="button"
                      className="max-button max-button-quiet"
                      onClick={onShareContact}
                      disabled={busy}
                    >
                      Взять номер из MAX
                    </button>
                    <button
                      type="button"
                      className="max-linklike"
                      onClick={() => setPhoneManual(true)}
                      disabled={busy}
                    >
                      Ввести вручную
                    </button>
                  </>
                )}
              </div>
            ) : null}

            {config.ask_comment ? (
              <label className="max-field">
                <span>Комментарий</span>
                <textarea
                  className="max-textarea"
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder={commentHint(config)}
                  disabled={busy}
                />
              </label>
            ) : null}
          </div>
        </>
      ) : null}

      {error ? (
        <div className="max-error" role="alert" style={{ marginTop: 14 }}>
          {error}
        </div>
      ) : null}

      <div className="max-submit-bar">
        {/* What exactly is being confirmed, right above the button - so the last tap is a
            confirmation rather than a leap of faith. */}
        {selectedEntries.length > 0 ? (
          <div className="max-summary">
            <span className="max-summary-text">
              {config.allow_multiple_items
                ? `${selectedCount} шт. · ${selectedEntries.length} поз.`
                : selectedItem}
              {selectedSlot ? ` · ${selectedSlot}` : ""}
            </span>
            {config.allow_multiple_items && hasPricedSelection ? (
              <span className="max-summary-price">{selectedTotal.toLocaleString("ru-RU")} ₽</span>
            ) : selectedEntries[0] && priceLabel(selectedEntries[0].item) ? (
              <span className="max-summary-price">{priceLabel(selectedEntries[0].item)}</span>
            ) : null}
          </div>
        ) : null}
        <button
          type="button"
          className="max-button"
          onClick={onSubmit}
          disabled={!canSubmit || busy}
        >
          {busy ? "Отправляем…" : config.cta_label}
        </button>
        {missing && !busy ? <p className="max-submit-hint">{missing}</p> : null}
      </div>
    </div>
  );
}
