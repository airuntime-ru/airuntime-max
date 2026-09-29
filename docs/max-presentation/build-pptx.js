/**
 * Builds presentation.pptx — the same deck as deck.html, for people who need to edit it
 * in PowerPoint rather than re-print the HTML.
 *
 *   npm install pptxgenjs --no-save && node build-pptx.js
 *
 * What pptxgenjs cannot draw - gradient fills, a repeating starfield, rounded image
 * corners - ships as images made by make-assets.py: bg-cover.jpg (also the HTML cover's
 * background), bg-dark.png, screen-*.png, card-owner-lead.png, bubble-owner.png,
 * qr-bot.png. That is why the dark slides, the cover and the closing slide look like their
 * HTML counterparts.
 *
 * Slide 1 never carries working tokens: this file is in git. The jury gets them in the PDF
 * printed by build-pdf.py --jury.
 */

const fs = require("fs");
const JSZip = require("jszip"); // pptxgenjs's own dependency
const pptxgen = require("pptxgenjs");

// The commit slide 1 points the jury at - keep it equal to the hash in deck.html.
const COMMIT = "eadc752d7bfc2ec65e96972c4c60e3be6547a8ab";

// --- palette -----------------------------------------------------------------------
const MAX_BLUE = "0077FF";
const MAX_VIOLET = "7B2CFF";
const INK = "0D1117";
const INK_2 = "4A5464";
const INK_3 = "79839A";
const LINE = "DDE4EE";
const SURFACE_2 = "F3F6FB";
const WHITE = "FFFFFF";
const ON_DARK = "EAF1FF";
const ON_DARK_2 = "A9B7CE";
const DARK_CARD = "141C2E";
const ACCENT_ON_DARK = "8EC4FF";
const PINK = "FF9AC4";
const GOOD = "1F8A55";
const HOT = "D31169";

const FONT = "Calibri";
const MONO = "Consolas";
const W = 13.33;
const H = 7.5;
const M = 0.55; // slide margin
const CW = W - M * 2; // content width

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // must be set before any slide is added
pres.author = "AIRuntime";
pres.title = "AIRuntime × MAX";

// --- building blocks ----------------------------------------------------------------

/** Footer + page number. The lockup is the only thing repeated on every slide. */
function chrome(slide, n, dark, label) {
  slide.addText(
    label
      ? [{ text: label, options: { bold: true } }]
      : [
          { text: "AIRUNTIME", options: { bold: true } },
          { text: "  ×  ", options: { color: dark ? "5C6B84" : INK_3 } },
          { text: "MAX", options: { bold: true } },
        ],
    {
      x: M,
      y: H - 0.52,
      w: 4,
      h: 0.3,
      fontFace: FONT,
      fontSize: 10,
      color: dark ? "8193AE" : INK_3,
      charSpacing: 1.2,
      isTextBox: true,
      margin: 0,
    }
  );
  slide.addText(String(n).padStart(2, "0"), {
    x: W - M - 1,
    y: H - 0.52,
    w: 1,
    h: 0.3,
    align: "right",
    fontFace: FONT,
    fontSize: 11,
    bold: true,
    color: dark ? "8193AE" : INK_3,
    isTextBox: true,
    margin: 0,
  });
}

function head(slide, eyebrow, title, sub, dark) {
  slide.addText(eyebrow.toUpperCase(), {
    x: M, y: 0.34, w: CW, h: 0.26,
    fontFace: FONT, fontSize: 11, bold: true, color: dark ? ACCENT_ON_DARK : MAX_BLUE, charSpacing: 2.2,
    isTextBox: true, margin: 0,
  });
  slide.addText(title, {
    x: M, y: 0.62, w: CW, h: 0.62,
    fontFace: FONT, fontSize: 32, bold: true, color: dark ? WHITE : INK,
    isTextBox: true, margin: 0,
  });
  if (sub) {
    slide.addText(sub, {
      x: M, y: 1.28, w: CW, h: 0.5, valign: "top",
      fontFace: FONT, fontSize: 14, color: dark ? ON_DARK_2 : INK_2, lineSpacingMultiple: 1.05,
      isTextBox: true, margin: 0,
    });
  }
}

/**
 * "plain **bold** plain" -> text runs. Bold runs take `strong`, the rest `base`: the deck
 * leads a line with its key words in ink and lets the explanation follow in grey.
 */
function rich(str, base, strong) {
  return str
    .split("**")
    .map((text, i) => ({ text, bold: i % 2 === 1 }))
    .filter((part) => part.text)
    .map((part) => ({
      text: part.text,
      options: part.bold ? { ...base, bold: true, color: strong } : { ...base },
    }));
}

/** Rounded card. A tint and a shadow set it apart - never an edge stripe. */
function card(slide, o) {
  let fill = SURFACE_2;
  let line = LINE;
  if (o.dark) {
    fill = o.accent ? "10284F" : DARK_CARD;
    line = o.accent ? "3E6DA8" : "2A3550";
  } else if (o.white) {
    fill = WHITE;
  } else if (o.tint) {
    fill = "EAF1FE";
    line = "C7DBFB";
  }
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h,
    rectRadius: 0.1,
    fill: { color: fill },
    line: { color: line, width: 1 },
    shadow: { type: "outer", color: "0B1730", blur: 10, offset: 2, angle: 90, opacity: 0.07 },
  });
}

/** Title + body inside a card; `**` in the body marks bold words. */
function cardText(slide, o) {
  const runs = [];
  if (o.title) {
    runs.push({ text: o.title, options: { bold: true, fontSize: o.titleSize || 15, color: o.titleColor || (o.dark ? WHITE : INK), breakLine: !!o.body } });
    if (o.body) runs.push({ text: " ", options: { fontSize: 5, breakLine: true } });
  }
  if (o.body) {
    runs.push(...rich(o.body, { fontSize: o.size || 12.5, color: o.dark ? ON_DARK_2 : INK_2 }, o.dark ? WHITE : INK));
  }
  slide.addText(runs, {
    x: o.x + 0.24, y: o.y + 0.2, w: o.w - 0.48, h: o.h - 0.4,
    fontFace: FONT, valign: "top", lineSpacingMultiple: 1.12,
    isTextBox: true, margin: 0,
  });
}

/** Bulleted list; `**` in an item marks its bold lead-in. */
function bullets(slide, o) {
  const runs = [];
  o.items.forEach((item, i) => {
    const parts = rich(item, { color: o.dark ? ON_DARK_2 : INK_2 }, o.dark ? WHITE : INK);
    parts[0].options.bullet = { code: "2022", indent: 15 };
    parts[0].options.paraSpaceAfter = o.gap ?? 5;
    if (i !== o.items.length - 1) parts[parts.length - 1].options.breakLine = true;
    runs.push(...parts);
  });
  slide.addText(runs, {
    x: o.x, y: o.y, w: o.w, h: o.h, valign: "top",
    fontFace: FONT, fontSize: o.size || 12.5, color: o.dark ? ON_DARK_2 : INK_2, lineSpacingMultiple: 1.08,
    isTextBox: true, margin: 0,
  });
}

/** Big number + label. A headline figure needs no chart. */
function stat(slide, o) {
  card(slide, { x: o.x, y: o.y, w: o.w, h: o.h, dark: o.dark });
  slide.addText(
    [
      { text: o.value, options: { fontSize: o.valueSize || 32, bold: true, color: o.dark ? WHITE : INK } },
      o.unit
        ? { text: " " + o.unit, options: { fontSize: 14, bold: true, color: o.dark ? ON_DARK_2 : INK_2 } }
        : { text: "" },
    ],
    { x: o.x + 0.2, y: o.y + 0.16, w: o.w - 0.4, h: 0.5, fontFace: FONT, isTextBox: true, margin: 0 }
  );
  slide.addText(o.label, {
    x: o.x + 0.2, y: o.y + 0.68, w: o.w - 0.4, h: o.h - 0.95, valign: "top",
    fontFace: FONT, fontSize: 11, color: o.dark ? ON_DARK_2 : INK_2, lineSpacingMultiple: 1.08,
    isTextBox: true, margin: 0,
  });
  if (o.src) {
    slide.addText(o.src, {
      x: o.x + 0.2, y: o.y + o.h - 0.32, w: o.w - 0.4, h: 0.24,
      fontFace: FONT, fontSize: 9, color: INK_3, isTextBox: true, margin: 0,
    });
  }
}

/** A small rounded label, like .pill / .tag in deck.html. Returns its width. */
function pill(slide, o) {
  const w = o.w || 0.16 + o.text.length * 0.075;
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w, h: 0.27, rectRadius: 0.06,
    fill: { color: o.fill }, line: { color: o.fill, width: 0 },
  });
  slide.addText(o.text, {
    x: o.x, y: o.y, w, h: 0.27, align: "center", valign: "middle",
    fontFace: FONT, fontSize: 9.5, bold: true, color: o.color, charSpacing: 0.4,
    isTextBox: true, margin: 0,
  });
  return w;
}

// Pill colours pre-blended over the card they sit on (the HTML uses rgba tints).
const PILL = {
  fact: { color: GOOD, fill: "D7E8E5" },
  guess: { color: HOT, fill: "F4E0EE" },
  blue: { color: MAX_BLUE, fill: "D8E8FB" },
  could: { color: MAX_VIOLET, fill: "E6E0FB" },
  wont: { color: INK_3, fill: "E1E4E9" },
};

/** A numbered square - the roadmap's step marker. */
function stepMark(slide, x, y, label) {
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w: 0.27, h: 0.27, rectRadius: 0.07,
    fill: { color: MAX_BLUE }, line: { color: MAX_BLUE, width: 0 },
  });
  slide.addText(label, {
    x, y, w: 0.27, h: 0.27, align: "center", valign: "middle",
    fontFace: FONT, fontSize: 11, bold: true, color: WHITE, isTextBox: true, margin: 0,
  });
}

/** deck.html is laid out in CSS pixels on a 1280px slide; at 13.33in that is 96px to the
 *  inch, so the cover and closing slides take their boxes straight from the HTML. */
function px(v) {
  return v / 96;
}

// Text on the cover background, pre-blended from the HTML's rgba(234,241,255,a) over the
// dark field - a solid colour survives every PowerPoint version, text transparency not.
const ON_COVER_LEAD = "BDC4D2"; // .80
const ON_COVER_SUB = "A5ACBC"; // .70
const ON_COVER_FILL = "8F96A6"; // .60
const ON_COVER_MUTED = "7B8190"; // .50

/** AIRuntime mark and wordmark x the MAX mark, at the HTML lockup's position. */
function lockup(slide, top) {
  slide.addImage({ path: "mark-airuntime.png", x: px(72), y: top, w: px(32), h: px(32) });
  slide.addText("AIRUNTIME", {
    x: px(113), y: top, w: px(140), h: px(32), valign: "middle",
    fontFace: FONT, fontSize: 14.25, bold: true, color: WHITE, charSpacing: 0.8, isTextBox: true, margin: 0,
  });
  slide.addText("×", {
    x: px(226), y: top, w: px(14), h: px(32), align: "center", valign: "middle",
    fontFace: FONT, fontSize: 14, color: "9DA6BA", isTextBox: true, margin: 0,
  });
  slide.addImage({ path: "mark-max.png", x: px(249), y: top, w: px(32), h: px(32) });
  slide.addText("max", {
    x: px(289), y: top, w: px(80), h: px(32), valign: "middle",
    fontFace: FONT, fontSize: 16, bold: true, color: WHITE, isTextBox: true, margin: 0,
  });
}

/**
 * A phone: rounded bezel with the screenshot inset in it. Same ratios as .phone in
 * deck.html - radius 8.2% and bezel 2.6% of the width - so the two decks match.
 * Returns the frame height, which is 1.948x the width; size frames by that height.
 */
function phone(slide, o) {
  const pad = o.w * 0.026;
  const sw = o.w - pad * 2;
  const h = sw * 2 + pad * 2;
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x,
    y: o.y,
    w: o.w,
    h,
    rectRadius: o.w * 0.082,
    fill: { color: o.dark ? "1E2638" : "DCE3EE" },
    line: { color: o.dark ? "39445E" : "CCD5E3", width: 0.75 },
    shadow: o.dark
      ? { type: "outer", color: "000000", blur: 30, offset: 12, angle: 90, opacity: 0.55 }
      : { type: "outer", color: "091228", blur: 16, offset: 6, angle: 90, opacity: 0.2 },
  });
  // Rounded corners are baked into screen-*.png (make-assets.py): pptxgenjs can only crop
  // an image to an ellipse.
  slide.addImage({ path: `screen-${o.name}.png`, x: o.x + pad, y: o.y + pad, w: sw, h: sw * 2 });
  return h;
}

/** Three phones in a row with a numbered caption under each - slides 8 and 9. */
function phoneRow(slide, shots) {
  const fw = 2.08;
  const top = 1.8;
  const gap = 0.4;
  const colw = (CW - gap * 2) / 3;
  shots.forEach(([name, title, note], i) => {
    const x = M + i * (colw + gap);
    const fh = phone(slide, { x: x + (colw - fw) / 2, y: top, w: fw, name });
    slide.addText(title, {
      x, y: top + fh + 0.1, w: colw, h: 0.28, align: "center",
      fontFace: FONT, fontSize: 13, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    slide.addText(note, {
      x: x + 0.15, y: top + fh + 0.4, w: colw - 0.3, h: 0.6, align: "center", valign: "top",
      fontFace: FONT, fontSize: 10.5, color: INK_2, lineSpacingMultiple: 1.08, isTextBox: true, margin: 0,
    });
  });
}

/** A bot message as MAX shows it: sender line, text, one inline button. */
function botMessage(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.16,
    fill: { color: WHITE }, line: { color: LINE, width: 1 },
    shadow: { type: "outer", color: "091228", blur: 18, offset: 6, angle: 90, opacity: 0.14 },
  });
  slide.addText(o.from.toUpperCase(), {
    x: o.x + 0.2, y: o.y + 0.16, w: o.w - 0.4, h: 0.22,
    fontFace: FONT, fontSize: 9.5, bold: true, color: MAX_BLUE, charSpacing: 1, isTextBox: true, margin: 0,
  });
  slide.addText(o.lines, {
    x: o.x + 0.2, y: o.y + 0.42, w: o.w - 0.4, h: o.h - 1.0, valign: "top",
    fontFace: FONT, fontSize: 12, color: INK, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0,
  });
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x + 0.2, y: o.y + o.h - 0.56, w: o.w - 0.4, h: 0.38, rectRadius: 0.09,
    fill: { color: "E5F1FF" }, line: { color: "E5F1FF", width: 0 },
  });
  slide.addText(o.button, {
    x: o.x + 0.2, y: o.y + o.h - 0.56, w: o.w - 0.4, h: 0.38, align: "center", valign: "middle",
    fontFace: FONT, fontSize: 11.5, bold: true, color: MAX_BLUE, isTextBox: true, margin: 0,
  });
}

/** A caption line under a picture. */
function caption(slide, text, x, y, w) {
  slide.addText(text, {
    x, y, w, h: 0.5, align: "center", valign: "top",
    fontFace: FONT, fontSize: 10.5, color: INK_3, lineSpacingMultiple: 1.08, isTextBox: true, margin: 0,
  });
}

function coverSlide() {
  const s = pres.addSlide();
  s.background = { path: "bg-cover.jpg" };
  return s;
}

function lightSlide() {
  const s = pres.addSlide();
  s.background = { color: WHITE };
  return s;
}

function darkSlide() {
  const s = pres.addSlide();
  s.background = { path: "bg-dark.png" };
  return s;
}

// ====================================================================================
// 1. Service slide
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Слайд 1 · служебный · не оценивается", "Техническая информация для проверки");

  const rows = [
    ["Чат-бот в MAX", "https://max.ru/t403_hakaton_max_bot", "«Хакатон МАХ 403», user_id 395683755", 0.62],
    ["Мини-приложение", "https://airuntime.ru/max", "Подключено к боту: кнопка «Открыть AIRuntime» и ссылки max.ru/t403_hakaton_max_bot?startapp=<slug>", 0.8],
    ["Git-репозиторий", "github.com/airuntime-ru/airuntime-max", "", 0.42],
    ["Commit hash", COMMIT, `ветка main · короткий ${COMMIT.slice(0, 7)}`, 0.62],
    ["Собственный API", "Не используется", "Эндпоинты /api/v1/max/* обслуживают только мини-приложение", 0.62],
    ["Тестовые учётки", "Не нужны: вход — аккаунт MAX", "Для роли клиента — второй аккаунт MAX", 0.62],
  ];
  let y = 1.42;
  for (const [k, v, sub, h] of rows) {
    s.addText(k, {
      x: M, y, w: 1.8, h: 0.28, valign: "top", fontFace: FONT, fontSize: 12, color: INK_3,
      isTextBox: true, margin: 0,
    });
    const code = /^(https:|github\.com|[0-9a-f]{40}$)/.test(v);
    s.addText(
      [
        { text: v, options: { fontSize: code ? 11.5 : 12.5, color: INK, bold: !code, fontFace: code ? MONO : FONT, breakLine: !!sub } },
        ...(sub ? [{ text: sub, options: { fontSize: 10.5, color: INK_3 } }] : []),
      ],
      { x: M + 1.85, y, w: 4.75, h: h - 0.04, valign: "top", fontFace: FONT, lineSpacingMultiple: 1.05, isTextBox: true, margin: 0 }
    );
    y += h;
  }

  card(s, { x: M, y: 5.12, w: 6.5, h: 1.66 });
  s.addText("Запуск и переменные окружения", {
    x: M + 0.24, y: 5.28, w: 6.0, h: 0.28, fontFace: FONT, fontSize: 14, bold: true, color: INK,
    isTextBox: true, margin: 0,
  });
  const t = (text, br) => ({ text, options: { breakLine: !!br } });
  const c = (text, br) => ({ text, options: { fontFace: MONO, color: INK, breakLine: !!br } });
  s.addText(
    [
      t("Все компоненты одной командой: "), c("docker compose up --build", true),
      c("MAX_BOT_TOKEN"), t(" = передаётся в PDF для жюри; в репозитории токенов нет", true),
      c("MAX_BOT_USERNAME"), t(" = "), c("t403_hakaton_max_bot"), t(" · "), c("MAX_WEBHOOK_SECRET"), t(" — любая строка", true),
      c("OPENAI_API_KEY"), t(" = по запросу; без ключа сервис собирается шаблоном", true),
      t("Остальное — "), c(".env.example"), t(", пункты README по заданию — раздел «Для проверки»"),
    ],
    {
      x: M + 0.24, y: 5.62, w: 6.05, h: 1.08, valign: "top", fontFace: FONT, fontSize: 10.5, color: INK_2,
      lineSpacingMultiple: 1.12, isTextBox: true, margin: 0,
    }
  );

  const cx = 7.35;
  card(s, { x: cx, y: 1.42, w: W - cx - M, h: 5.36, tint: true });
  s.addText("Порядок прохождения основного сценария", {
    x: cx + 0.28, y: 1.64, w: W - cx - M - 0.56, h: 0.3,
    fontFace: FONT, fontSize: 15, bold: true, color: INK, isTextBox: true, margin: 0,
  });
  bullets(s, {
    x: cx + 0.28, y: 2.1, w: W - cx - M - 0.56, h: 4.5, size: 12.5, gap: 8,
    items: [
      "Открыть max.ru/t403_hakaton_max_bot, нажать «Начать», затем «Открыть AIRuntime»",
      "Описать бизнес **одним сообщением**: «Автосервис на Лесной. Диагностика 1500, замена масла 900, шиномонтаж 2400. Работаем с 9 до 20» — и нажать «Собрать витрину»",
      "Когда индикатор сборки сменится обложкой, витрина опубликована: «Поделиться», «Открыть», ссылка",
      "Открыть ссылку **со второго аккаунта MAX** — это роль клиента",
      "Выбрать услугу и время, указать имя, нажать «Записаться»",
      "Владельцу в чат с ботом придёт заявка с кнопкой «Открыть заявки»",
      "Нажать «Принять» — клиенту придёт ответ в MAX",
    ],
  });
  chrome(s, 1, false);
}

// ====================================================================================
// 2. Cover
// ====================================================================================
// The owner's one message, the bot's answer with its button, and the storefront that
// button opens - the product told as the chat it happens in. Boxes are deck.html's.
{
  const s = coverSlide();
  const small = { fontFace: FONT, fontSize: 8.6, bold: true, charSpacing: 2, isTextBox: true, margin: 0 };

  lockup(s, px(140));
  s.addText("ТРЕК «ЭФФЕКТИВНЫЙ БИЗНЕС»", { ...small, x: px(72), y: px(204), w: px(520), h: px(20), color: ACCENT_ON_DARK });
  s.addText("AIRuntime", {
    x: px(68), y: px(226), w: px(560), h: px(124), valign: "top",
    fontFace: FONT, fontSize: 64, bold: true, color: WHITE, isTextBox: true, margin: 0,
  });
  s.addText(
    "Витрина с онлайн-записью и заказами в MAX из одного сообщения — со своим дизайном, без разработчика и без ожидания.",
    {
      x: px(72), y: px(368), w: px(500), h: px(92), valign: "top",
      fontFace: FONT, fontSize: 16, color: ON_COVER_LEAD, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0,
    }
  );

  // Team: still to be filled in, so it is drawn as a field rather than as finished copy.
  const field = { style: "dash", color: "59627A" };
  s.addText("КОМАНДА", { ...small, x: px(72), y: px(505), w: px(200), h: px(16), color: ON_COVER_MUTED });
  s.addText("[Название команды]", {
    x: px(72), y: px(528), w: px(210), h: px(24), fontFace: FONT, fontSize: 12.75, bold: true,
    color: ON_COVER_FILL, underline: field, isTextBox: true, margin: 0,
  });
  s.addText("[Участники и роли]", {
    x: px(72), y: px(556), w: px(210), h: px(20), fontFace: FONT, fontSize: 10,
    color: ON_COVER_FILL, underline: field, isTextBox: true, margin: 0,
  });
  s.addText("РЕШЕНИЕ", { ...small, x: px(298), y: px(505), w: px(250), h: px(16), color: ON_COVER_MUTED });
  s.addText("Чат-бот + мини-приложение", {
    x: px(298), y: px(528), w: px(270), h: px(24), fontFace: FONT, fontSize: 12.75, bold: true,
    color: WHITE, isTextBox: true, margin: 0,
  });
  s.addText("@t403_hakaton_max_bot", {
    x: px(298), y: px(556), w: px(270), h: px(20), fontFace: FONT, fontSize: 10,
    color: ON_COVER_SUB, isTextBox: true, margin: 0,
  });

  // The owner's message.
  s.addText("ВЛАДЕЛЕЦ ОПИСЫВАЕТ БИЗНЕС", { ...small, fontSize: 8.25, x: px(614), y: px(104), w: px(300), h: px(16), color: ON_COVER_MUTED });
  s.addImage({ path: "bubble-owner.png", x: px(614), y: px(132), w: px(272), h: px(110) });
  s.addText(
    "Автосервис на Лесной пр. 12. Диагностика подвески 1500, замена масла 900, шиномонтаж 2400. С 9 до 20, +7 812 000-00-00",
    {
      x: px(631), y: px(142), w: px(240), h: px(90), valign: "middle",
      fontFace: FONT, fontSize: 11, color: WHITE, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0,
    }
  );

  // The storefront - before the answer, so the answer is drawn over its empty lower half.
  phone(s, { x: px(912), y: px(76), w: px(290), name: "storefront", dark: true });

  // The bot's answer, with the open_app button that opens the phone above.
  s.addShape(pres.ShapeType.roundRect, {
    x: px(640), y: px(404), w: px(300), h: px(146), rectRadius: px(20),
    fill: { color: WHITE }, line: { color: WHITE, width: 0 },
    shadow: { type: "outer", color: "000000", blur: 30, offset: 12, angle: 90, opacity: 0.5 },
  });
  s.addShape(pres.ShapeType.ellipse, {
    x: px(658), y: px(420), w: px(22), h: px(22), fill: { color: GOOD }, line: { color: GOOD, width: 0 },
  });
  s.addText("✓", {
    x: px(658), y: px(420), w: px(22), h: px(22), align: "center", valign: "middle",
    fontFace: "Segoe UI Symbol", fontSize: 9.5, bold: true, color: WHITE, isTextBox: true, margin: 0,
  });
  s.addText("Витрина готова", {
    x: px(689), y: px(418), w: px(230), h: px(26), valign: "middle",
    fontFace: FONT, fontSize: 12.5, bold: true, color: INK, isTextBox: true, margin: 0,
  });
  s.addText("Клиенты записываются по ссылке, заявки приходят в чат с ботом.", {
    x: px(658), y: px(448), w: px(264), h: px(40), valign: "top",
    fontFace: FONT, fontSize: 10.5, color: INK_2, lineSpacingMultiple: 1.08, isTextBox: true, margin: 0,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: px(658), y: px(498), w: px(264), h: px(37), rectRadius: px(11),
    fill: { color: MAX_BLUE }, line: { color: MAX_BLUE, width: 0 },
  });
  s.addText("Поделиться ссылкой", {
    x: px(658), y: px(498), w: px(264), h: px(37), align: "center", valign: "middle",
    fontFace: FONT, fontSize: 11, bold: true, color: WHITE, isTextBox: true, margin: 0,
  });

  chrome(s, 2, true, "ХАКАТОН MAX  ·  2026");
}

// ====================================================================================
// 3. Executive summary
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Executive summary", "AIRuntime: запись в MAX из одного сообщения");
  s.addText(
    "Владелец микробизнеса описывает бизнес одним сообщением в мини-приложении MAX — и получает опубликованную витрину со своим дизайном и онлайн-записью или заказом. Клиенты записываются, не выходя из MAX, заявки приходят владельцу в чат. Сейчас — запись и заказы; та же механика подходит десяткам ниш (слайд 17).",
    { x: M, y: 1.4, w: CW, h: 0.9, valign: "top", fontFace: FONT, fontSize: 15, color: INK_2, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );

  const cw = (CW - 0.2 * 3) / 4;
  [
    ["Для кого", "Самозанятые и микро-ИП в услугах с записью на время: автосервис, барбершоп, маникюр, репетитор. 1–5 человек, без сайта и CRM."],
    ["Проблема", "Запись ведётся вручную в личных сообщениях: ночные заявки теряются, случаются двойные брони, прайс пересказывается каждому."],
    ["Решение", "Чат-бот и мини-приложение в MAX: описание → сервис с услугами, ценами и временем → запись → заявка владельцу → ответ клиенту."],
    ["Результат", "Запись круглосуточно и без переписки: клиент проходит путь сам за два касания, владельцу остаётся одна кнопка."],
  ].forEach(([t, b], i) => {
    const x = M + i * (cw + 0.2);
    card(s, { x, y: 2.42, w: cw, h: 2.1, tint: true });
    cardText(s, { x, y: 2.42, w: cw, h: 2.1, title: t, body: b, size: 12.5 });
  });

  [
    ["1", "", "сообщение от владельца до готового сервиса"],
    ["84", "", "автотеста, включая подделку подписи и сквозной сценарий"],
    ["2", "", "касания клиента: выбрать услугу и время"],
    ["0", "", "строк кода и сторонних сервисов у предпринимателя"],
  ].forEach(([v, u, l], i) => {
    stat(s, { x: M + i * (cw + 0.2), y: 4.72, w: cw, h: 1.55, value: v, unit: u, label: l });
  });
  chrome(s, 3, false);
}

// ====================================================================================
// 4. Audience & problem
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Пользовательская ценность · аудитория и проблема", "Кто именно и что именно болит");

  const lw = 6.45;
  card(s, { x: M, y: 1.45, w: lw, h: 1.62 });
  cardText(s, {
    x: M, y: 1.45, w: lw, h: 1.62, title: "Приоритетный сегмент", size: 12.5,
    body: "Владелец микробизнеса услуг с записью на время: 1–5 человек, без сайта и CRM, запись сейчас — в личных сообщениях. Пилотные ниши — автосервис, барбершоп, мастер маникюра. **Не в пилоте**: сети и франшизы — там решает головной офис.",
  });

  card(s, { x: M, y: 3.22, w: lw, h: 2.72, tint: true });
  s.addText("Формулировка проблемы", {
    x: M + 0.26, y: 3.42, w: lw - 0.52, h: 0.3, fontFace: FONT, fontSize: 15, bold: true, color: MAX_BLUE, isTextBox: true, margin: 0,
  });
  s.addText(
    rich(
      "**Владелец микросервиса в сфере услуг** в ситуации, когда клиенты пишут в личные сообщения в любое время, **хочет** принимать записи без ручного согласования каждой, **но сталкивается с тем**, что онлайн-запись требует платного сервиса с настройкой либо разработки сайта или бота, **из-за чего** ведёт запись вручную — теряет ночные заявки, допускает двойные брони и тратит рабочее время на переписку.",
      { color: INK_2 },
      INK
    ),
    { x: M + 0.26, y: 3.85, w: lw - 0.52, h: 2.0, valign: "top", fontFace: FONT, fontSize: 14, lineSpacingMultiple: 1.2, isTextBox: true, margin: 0 }
  );

  const rx = 7.2;
  const rw = W - M - rx;
  const pw = pill(s, { x: rx, y: 1.45, text: "Факты", ...PILL.fact, fill: "E3F1EB" });
  s.addText("размер и динамика аудитории", {
    x: rx + pw + 0.1, y: 1.45, w: 3.5, h: 0.27, valign: "middle", fontFace: FONT, fontSize: 11, color: INK_3, isTextBox: true, margin: 0,
  });
  const tw = (rw - 0.14 * 2) / 3;
  [
    ["6,6", "млн", "субъектов МСП в реестре", "ФНС России, июль 2026"],
    ["81,8", "%", "юрлиц и ИП — это МСП", "ФНС России, 2026"],
    ["+3,5", "%", "рост за год", "ФНС России, 2026"],
  ].forEach(([v, u, l, src], i) => {
    stat(s, { x: rx + i * (tw + 0.14), y: 1.85, w: tw, h: 1.62, value: v, unit: u, label: l, src, valueSize: 26 });
  });
  card(s, { x: rx, y: 3.62, w: rw, h: 1.15, white: true });
  s.addText(
    rich("**Задача названа в задании трека:** ценность цифрового решения — во взаимодействии с клиентами, «например, оформления заказа, записи на услугу, бронирования… непосредственно в MAX».", { color: INK_2 }, INK),
    { x: rx + 0.22, y: 3.78, w: rw - 0.44, h: 0.9, valign: "top", fontFace: FONT, fontSize: 12, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  pill(s, { x: rx, y: 4.95, text: "Допущение", ...PILL.guess, fill: "FFE8F2" });
  s.addText(
    "Доля микросервисов, которые ведут запись вручную и считают это проблемой, исследованием не подтверждена. Её проверяет пилот (слайд 20), допущения собраны на слайде 21.",
    { x: rx, y: 5.32, w: rw, h: 0.8, valign: "top", fontFace: FONT, fontSize: 11, color: INK_3, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0 }
  );
  chrome(s, 4, false);
}

// ====================================================================================
// 5. As Is / To Be
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Пользовательская ценность · участок процесса", "Что меняется в пользовательском пути",
    "Мы не автоматизируем весь бизнес-процесс — только приём и подтверждение записи, где эффект заметен сразу.");

  const cw = (CW - 0.3) / 2;
  card(s, { x: M, y: 1.85, w: cw, h: 3.1 });
  pill(s, { x: M + 0.26, y: 2.05, text: "As Is — сегодня", ...PILL.guess });
  bullets(s, {
    x: M + 0.26, y: 2.5, w: cw - 0.52, h: 2.3, size: 12.5,
    items: [
      "Клиент пишет в личные сообщения или звонит",
      "Владелец отвечает вручную, сверяется с блокнотом",
      "Согласование времени занимает несколько сообщений",
      "Ночные заявки остаются без ответа",
      "Прайс приходится пересказывать каждому заново",
      "Подтверждение — снова вручную",
    ],
  });

  const x2 = M + cw + 0.3;
  card(s, { x: x2, y: 1.85, w: cw, h: 3.1, tint: true });
  pill(s, { x: x2 + 0.26, y: 2.05, text: "To Be — с AIRuntime", ...PILL.fact, fill: "D5EBE6" });
  bullets(s, {
    x: x2 + 0.26, y: 2.5, w: cw - 0.52, h: 2.3, size: 12.5,
    items: [
      "**Исчезает** пересказ прайса: услуги и цены видны в самом сервисе",
      "**Упрощается** выбор времени: клиент берёт слот сам",
      "**Автоматически** собирается заявка и приходит в чат владельца",
      "**Быстрее** ответ: подтверждение — одна кнопка, клиенту приходит сообщение",
      "**Проще** результат: запись принимается круглосуточно",
      "Владелец по-прежнему решает сам — продукт не назначает встречи за него",
    ],
  });

  card(s, { x: M, y: 5.15, w: CW, h: 1.3, white: true });
  s.addText(
    rich("**Гипотеза.** Если мы поможем владельцу микросервиса принимать записи сервисом в MAX, собранным из одного сообщения, доля заявок, дошедших до подтверждения, вырастет, а время на переписку сократится — потому что клиент проходит путь сам, а владельцу остаётся одно решение. Как это проверить — слайд 11.", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.33, w: CW - 0.56, h: 0.98, valign: "top", fontFace: FONT, fontSize: 13.5, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );
  chrome(s, 5, false);
}

// ====================================================================================
// 6. Versus today
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Пользовательская ценность · преимущество", "Почему не так, как запись ведут сейчас",
    "Сравнение по тому, что важно владельцу микробизнеса. Цены и сроки альтернатив мы не исследовали, поэтому сравниваем качественно.");

  const OURS = "EAF3FF";
  const rule = { type: "solid", pt: 1, color: LINE };
  const none = { type: "none" };
  const border = [none, none, rule, none];
  const th = (text, ours) => ({
    text: text.toUpperCase(),
    options: { bold: true, fontSize: 9.5, color: ours ? MAX_BLUE : INK_3, fill: { color: ours ? OURS : WHITE }, border, charSpacing: 0.6 },
  });
  const td = (text, i) => ({
    text,
    options: {
      fontSize: 11.5,
      bold: i === 0 || i === 4,
      color: i === 0 || i === 4 ? INK : INK_2,
      fill: { color: i === 4 ? OURS : WHITE },
      border,
    },
  });
  const rows = [
    ["Запуск", "уже есть", "регистрация, настройка услуг и расписания", "задание, подрядчик, ожидание", "одно сообщение — готовая витрина"],
    ["Внешний вид", "—", "типовой шаблон сервиса", "свой дизайн, но недели работы", "свой дизайн под бренд, сразу"],
    ["Кто нужен", "никто", "владелец разбирается в настройках", "разработчик", "никто: описание обычными словами"],
    ["Где записывается клиент", "в переписке", "на отдельной странице или в приложении сервиса", "на сайте или в отдельном боте", "внутри MAX, по ссылке или QR-коду"],
    ["Заявка ночью", "ждёт ответа до утра", "принимается", "принимается", "принимается и сразу приходит в чат"],
    ["Прайс и свободное время", "пересказываются каждому", "видны клиенту", "видны клиенту", "видны, правятся одной фразой"],
    ["Телефон клиента", "пишет вручную", "вводит в форму", "вводит в форму", "одной кнопкой из аккаунта MAX"],
  ];
  const restW = (CW - 1.95) / 4;
  s.addTable(
    [
      [th(""), th("Переписка в личных сообщениях"), th("Сервис онлайн-записи или CRM"), th("Сайт или бот на заказ"), th("AIRuntime в MAX", true)],
      ...rows.map((r) => r.map((cell, i) => td(cell, i))),
    ],
    {
      x: M, y: 1.95, w: CW, colW: [1.95, restW, restW, restW, restW],
      rowH: [0.5, 0.56, 0.56, 0.56, 0.56, 0.56, 0.56, 0.56],
      fontFace: FONT, valign: "top", margin: [0.07, 0.1, 0.05, 0.1],
    }
  );
  s.addText("Где мы сознательно слабее: нет онлайн-оплаты и календаря занятости — это границы MVP, а не упущение (слайд 12).", {
    x: M, y: 6.5, w: CW, h: 0.3, fontFace: FONT, fontSize: 11, color: INK_3, isTextBox: true, margin: 0,
  });
  chrome(s, 6, false);
}

// ====================================================================================
// 7. Scenario: owner - real screens
// ====================================================================================
{
  const s = lightSlide();
  head(s, "UX · основной сценарий, часть 1", "Владелец: одно сообщение → опубликованная витрина",
    "Вход — «Начать» в чат-боте, затем «Открыть AIRuntime». Регистрации нет: аккаунт MAX и есть вход.");
  phoneRow(s, [
    ["owner-compose", "1. Описывает бизнес", "Одним сообщением, как рассказал бы другу: есть готовые примеры. Сайт и файлы — по желанию, не обязательны."],
    ["owner-building", "2. Видит, что идёт сборка", "Модель придумывает дизайн и собирает витрину; если закрыть приложение случайно, MAX переспросит."],
    ["owner-ready", "3. Получает результат", "Обложка витрины в её цветах, «Поделиться» в чаты MAX, «Открыть» глазами клиента; заявки и правка словами — во вкладках."],
  ]);
  chrome(s, 7, false);
}

// ====================================================================================
// 8. Scenario: customer - real screens
// ====================================================================================
{
  const s = lightSlide();
  head(s, "UX · основной сценарий, часть 2", "Клиент: два касания до записи",
    "Клиент открывает ссылку или QR-код владельца — сервис сразу внутри MAX, без установки и регистрации.");
  phoneRow(s, [
    ["storefront", "1. Выбор", "Услуги с ценами и длительностью, адрес и часы в шапке. Время появляется после выбора услуги."],
    ["booking", "2. Отправка", "Имя подставлено из профиля MAX, телефон — одной кнопкой. Над кнопкой видно, что именно бронируется."],
    ["customer-done", "3. Готово", "Заявка принята: услуга и время на экране, а ответ владельца придёт сюда, в MAX."],
  ]);
  chrome(s, 8, false);
}

// ====================================================================================
// 9. Design gallery - five briefs, five unrelated looks
// ====================================================================================
{
  const s = lightSlide();
  head(s, "UX · дизайн витрины", "Каждая витрина — со своим дизайном",
    "Модель работает как арт-директор: по описанию, сайту и фото выбирает палитру, шрифт, первый экран и вид каталога. Пять бизнесов — пять разных витрин, один движок.");
  const shots = [
    ["gallery-coffee", "Кофейня", "фото с сайта, меню карточками, бегущая строка"],
    ["gallery-barber", "Барбершоп", "тёмная тема, плакатный шрифт, прайс как в меню"],
    ["gallery-tutor", "Репетитор", "журнальная вёрстка, программы по номерам"],
    ["gallery-nails", "Маникюр", "мягкая пастель, плитки процедур"],
    ["gallery-lawyer", "Юрист", "строгая антиква, одна кнопка заявки"],
  ];
  const fw = 1.66;
  const top = 1.95;
  const gap = (CW - fw * 5) / 4;
  shots.forEach(([name, title, note], i) => {
    const x = M + i * (fw + gap);
    const fh = phone(s, { x, y: top, w: fw, name, dark: name === "gallery-barber" });
    s.addText(title, {
      x: x - 0.25, y: top + fh + 0.08, w: fw + 0.5, h: 0.26, align: "center",
      fontFace: FONT, fontSize: 12.5, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    s.addText(note, {
      x: x - 0.25, y: top + fh + 0.34, w: fw + 0.5, h: 0.4, align: "center", valign: "top",
      fontFace: FONT, fontSize: 9.5, color: INK_3, lineSpacingMultiple: 1.05, isTextBox: true, margin: 0,
    });
  });
  card(s, { x: M, y: 6.1, w: CW, h: 0.72, tint: true });
  s.addText(
    rich("**Свобода без поломок:** модель выбирает из закрытого словаря — 8 кириллических шрифтов, 5 композиций первого экрана, 6 видов каталога, фактуры — и сама составляет палитру; сервер проверяет контраст и отбрасывает нечитаемое. Кода модель не пишет, поэтому любая витрина остаётся удобной на телефоне.", { color: INK_2 }, INK),
    { x: M + 0.24, y: 6.17, w: CW - 0.48, h: 0.6, valign: "middle", fontFace: FONT, fontSize: 11, lineSpacingMultiple: 1.08, isTextBox: true, margin: 0 }
  );
  chrome(s, 9, false);
}

// ====================================================================================
// 10. Feedback loop: lead -> owner's decision -> answer to the customer
// ====================================================================================
{
  const s = lightSlide();
  head(s, "UX · обратная связь", "Заявка → решение владельца → ответ клиенту");

  const mw = 3.72;
  botMessage(s, {
    x: M, y: 1.72, w: mw, h: 2.1, from: "Бот · владельцу", button: "Открыть заявки",
    lines: [
      { text: "Новая заявка", options: { bold: true } },
      { text: " · Автосервис на Лесной", options: { breakLine: true } },
      { text: "Диагностика подвески · Чт 25 сен, 14:00", options: { breakLine: true } },
      { text: "Пётр Клиент, +7 999 000-11-22", options: { breakLine: true } },
      { text: "«Mazda 6, стучит подвеска»", options: { italic: true, color: INK_3 } },
    ],
  });
  caption(s, "Приходит в чат с ботом сразу, в любое время суток", M, 4.12, mw);

  // The owner's side, cut from the real screen at full size: a whole phone at this width
  // would shrink the one thing that matters - the buttons - into illegibility.
  const cx = M + mw + 0.4;
  const cwid = CW - 2 * (mw + 0.4);
  const inner = cwid - 0.14;
  const innerH = (inner * 432) / 740;
  s.addShape(pres.ShapeType.roundRect, {
    x: cx, y: 1.62, w: cwid, h: innerH + 0.14, rectRadius: 0.18,
    fill: { color: "F2F4F7" }, line: { color: "E3E8EF", width: 0.75 },
    shadow: { type: "outer", color: "091228", blur: 18, offset: 6, angle: 90, opacity: 0.18 },
  });
  s.addImage({ path: "card-owner-lead.png", x: cx + 0.07, y: 1.69, w: inner, h: innerH });
  caption(s, "В кабинете — «Принять» или «Отклонить»; рядом — позвонить или написать клиенту", cx, 4.12, cwid);

  const rx = W - M - mw;
  botMessage(s, {
    x: rx, y: 1.85, w: mw, h: 1.85, from: "Бот · клиенту", button: "Открыть AIRuntime",
    lines: [
      { text: "Запись подтверждена", options: { bold: true, breakLine: true } },
      { text: "Автосервис на Лесной", options: { breakLine: true } },
      { text: "Диагностика подвески · Чт 25 сен, 14:00" },
    ],
  });
  caption(s, "Клиенту в MAX — туда же, где он записывался. При отказе — кнопка «Выбрать другое время»", rx, 4.12, mw);

  [M + mw + 0.06, rx - 0.34].forEach((ax) => {
    s.addText("→", {
      x: ax, y: 2.55, w: 0.28, h: 0.4, align: "center", valign: "middle",
      fontFace: FONT, fontSize: 22, color: INK_3, isTextBox: true, margin: 0,
    });
  });

  card(s, { x: M, y: 5.0, w: CW, h: 1.25 });
  const sw = (CW - 0.48 - 0.25 * 3) / 4;
  [
    ["Загрузка", "Скелетоны экрана и индикатор сборки страницы"],
    ["Результат", "«Витрина готова», «Заявка принята», статусы заявок"],
    ["Ошибка", "Понятный текст и «Повторить»; введённое не теряется"],
    ["Без тупиков", "Каждое сообщение бота открывает нужный экран; в предпросмотре — «Назад»"],
  ].forEach(([t, b], i) => {
    s.addText(
      [
        { text: t, options: { bold: true, fontSize: 12.5, color: INK, breakLine: true } },
        { text: b, options: { fontSize: 11, color: INK_2 } },
      ],
      { x: M + 0.24 + i * (sw + 0.25), y: 5.17, w: sw, h: 0.95, valign: "top", fontFace: FONT, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0 }
    );
  });
  chrome(s, 10, false);
}

// ====================================================================================
// 11. Expected effect
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Обоснованность · ожидаемый эффект", "Что должно измениться и как это проверить",
    "Улучшаем один участок — приём и подтверждение записи. На этапе хакатона это гипотезы; важно, что каждую можно измерить после пилота.");

  const cw = (CW - 0.3) / 2;
  card(s, { x: M, y: 1.95, w: cw, h: 4.1 });
  s.addText("Метрики, которые снимает сам продукт", {
    x: M + 0.26, y: 2.15, w: cw - 0.52, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: INK, isTextBox: true, margin: 0,
  });
  bullets(s, {
    x: M + 0.26, y: 2.6, w: cw - 0.52, h: 2.9, size: 12.5, gap: 7,
    items: [
      "**Доля доведённых записей** — открыл страницу записи → отправил заявку",
      "**Время до подтверждения** — от заявки до нажатия владельцем кнопки",
      "**Доля заявок вне рабочих часов** — прямой замер того, что раньше терялось",
      "**Ручных сообщений на одну запись** — целевое значение 0",
      "**Время от «описал» до первой заявки** — скорость выхода на результат",
    ],
  });
  s.addText("Все события уже проходят через наш бэкенд — отдельная аналитика для замера не нужна.", {
    x: M + 0.26, y: 5.42, w: cw - 0.52, h: 0.5, valign: "top", fontFace: FONT, fontSize: 10.5, color: INK_3, isTextBox: true, margin: 0,
  });

  const x2 = M + cw + 0.3;
  card(s, { x: x2, y: 1.95, w: cw, h: 4.1, tint: true });
  s.addText("Как измеряем", {
    x: x2 + 0.26, y: 2.15, w: cw - 0.52, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: INK, isTextBox: true, margin: 0,
  });
  bullets(s, {
    x: x2 + 0.26, y: 2.6, w: cw - 0.52, h: 1.85, size: 12.5, gap: 7,
    items: [
      "Базовая линия снимается **до** запуска: владелец неделю считает заявки и время на переписку вручную",
      "Пилот — три недели через AIRuntime, сравнение по тем же величинам",
      "Сравниваем на одном и том же бизнесе, а не между разными",
    ],
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: x2 + 0.26, y: 4.4, w: cw - 0.52, h: 1.4, rectRadius: 0.08,
    fill: { color: WHITE }, line: { color: "C7DBFB", width: 1 },
  });
  s.addText(
    rich("**Критерий успеха пилота:** владелец после пилота не возвращается к ручной записи, и хотя бы одна заявка пришла вне рабочих часов и была подтверждена.", { color: INK_2 }, INK),
    { x: x2 + 0.46, y: 4.58, w: cw - 0.92, h: 1.1, valign: "top", fontFace: FONT, fontSize: 13, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );
  chrome(s, 11, false);
}

// ====================================================================================
// 12. MVP scope (MoSCoW)
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Обоснованность · границы MVP", "Что вошло в MVP и что сознательно нет",
    "MVP закрывает один сценарий от описания до ответа клиенту. Приоритеты — по MoSCoW; Won't Have — осознанное ограничение объёма.");

  const cw = (CW - 0.2 * 3) / 4;
  [
    ["MUST", PILL.fact, "сделано", ["Описание → опубликованный сервис", "Запись клиента внутри MAX", "Заявка владельцу в чат", "Подтверждение и ответ клиенту"]],
    ["SHOULD", PILL.blue, "сделано", ["Свой дизайн у каждой витрины", "Правка словами: тексты, цены, стиль", "Скрыть, опубликовать, удалить", "«Поделиться» в чаты MAX", "Телефон из аккаунта MAX", "Просмотр глазами клиента"]],
    ["COULD", PILL.could, "после пилота", ["Напоминание клиенту накануне", "Календарь занятости вместо текстовых слотов", "Тип «аренда»: сутки и залог"]],
    ["WON'T", PILL.wont, "сознательно", ["Онлайн-оплата: сервис доводит до заявки", "Сотрудники и роли", "Интеграции с CRM и внешними календарями", "Код под каждый бизнес: сервис — это данные"]],
  ].forEach(([tag, colours, label, items], i) => {
    const x = M + i * (cw + 0.2);
    card(s, { x, y: 1.95, w: cw, h: 2.95 });
    const tw = pill(s, { x: x + 0.24, y: 2.15, text: tag, ...colours });
    s.addText(label, {
      x: x + 0.24 + tw + 0.1, y: 2.15, w: cw - tw - 0.6, h: 0.27, valign: "middle",
      fontFace: FONT, fontSize: 13.5, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    bullets(s, { x: x + 0.24, y: 2.6, w: cw - 0.48, h: 2.2, size: 12, gap: 5, items });
  });

  card(s, { x: M, y: 5.1, w: CW, h: 0.95, tint: true });
  s.addText(
    rich("**Почему границы именно такие:** каждый пункт Must нужен, чтобы клиент дошёл от ссылки до подтверждённой записи; без любого из них сценарий обрывается. Всё, что требует денег клиента, сотрудников или чужих систем, откладываем до проверки главной гипотезы.", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.25, w: CW - 0.56, h: 0.7, valign: "top", fontFace: FONT, fontSize: 13, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );
  chrome(s, 12, false);
}

// ====================================================================================
// 13. Architecture (dark)
// ====================================================================================
{
  const s = darkSlide();
  head(s, "Техническая оценка · архитектура", "Состав решения и почему он такой", null, true);

  const colTitles = ["МЕССЕНДЖЕР MAX", "БЭКЕНД AIRUNTIME", "ХРАНЕНИЕ И ИНФРАСТРУКТУРА"];
  const colX = [M, 5.0, 9.4];
  const colW = [3.55, 3.9, 3.38];
  colTitles.forEach((t, i) => {
    s.addText(t, {
      x: colX[i], y: 1.42, w: colW[i], h: 0.25,
      fontFace: FONT, fontSize: 10, bold: true, color: ACCENT_ON_DARK, charSpacing: 1.8, isTextBox: true, margin: 0,
    });
  });

  const boxH = 0.78;
  const rowY = [1.75, 2.7, 3.65];
  [
    [0, 0, "Чат-бот", "приветствие и уведомления", true],
    [0, 1, "Мини-приложение", "создание, сервис, заявки", true],
    [1, 0, "Вебхук", "секрет в пути, всегда 200", false],
    [1, 1, "API мини-аппа", "вход — подпись initData", false],
    [1, 2, "Генератор сервиса", "LLM → контент + дизайн, валидация", false],
    [2, 0, "PostgreSQL", "владельцы, сервисы, заявки", false],
    [2, 1, "Docker + Traefik", "HTTPS, сертификат Минцифры", false],
    [2, 2, "LLM-провайдер", "внешний, с фоллбэком", false],
  ].forEach(([c, r, t, sub, accent]) => {
    s.addShape(pres.ShapeType.roundRect, {
      x: colX[c], y: rowY[r], w: colW[c], h: boxH, rectRadius: 0.09,
      fill: { color: accent ? "10284F" : DARK_CARD },
      line: { color: accent ? "3E6DA8" : "2A3550", width: 1 },
    });
    s.addText(t, {
      x: colX[c] + 0.22, y: rowY[r] + 0.12, w: colW[c] - 0.44, h: 0.28,
      fontFace: FONT, fontSize: 13.5, bold: true, color: WHITE, isTextBox: true, margin: 0,
    });
    s.addText(sub, {
      x: colX[c] + 0.22, y: rowY[r] + 0.42, w: colW[c] - 0.44, h: 0.26,
      fontFace: FONT, fontSize: 10.5, color: ON_DARK_2, isTextBox: true, margin: 0,
    });
  });

  const arrow = { color: "7FB6FF", width: 1.5, endArrowType: "triangle" };
  [[0, 0], [0, 1], [1, 0], [1, 1], [1, 2]].forEach(([c, r]) => {
    s.addShape(pres.ShapeType.line, {
      x: colX[c] + colW[c] + 0.06, y: rowY[r] + boxH / 2, w: colX[c + 1] - colX[c] - colW[c] - 0.12, h: 0,
      line: { ...arrow },
    });
  });
  // The mini-app API hands descriptions to the generator.
  s.addShape(pres.ShapeType.line, {
    x: colX[1] + colW[1] / 2, y: rowY[1] + boxH + 0.02, w: 0, h: rowY[2] - rowY[1] - boxH - 0.04,
    line: { ...arrow },
  });
  [["обновления", 0], ["initData", 1]].forEach(([label, r]) => {
    s.addText(label, {
      x: colX[0] + colW[0] + 0.08, y: rowY[r] + 0.1, w: 0.85, h: 0.2,
      fontFace: FONT, fontSize: 8.5, color: "8C9AB3", isTextBox: true, margin: 0,
    });
  });

  const cw3 = (CW - 0.25 * 2) / 3;
  [
    ["Витрина — это данные, а не код", "Модель заполняет схему и выбирает дизайн из закрытого словаря, но не пишет программу: один мультитенантный мини-апп рендерит все витрины. Кривое поле обрезает валидация, нечитаемую палитру сервер отбрасывает."],
    ["Бот — вход, мини-приложение — работа", "Как советует задание: чат-бот — для уведомлений и коротких действий, форма, каталог и список заявок — в мини-приложении. Первая версия с мастером в чате делала сервис из любого сообщения, мы её заменили."],
    ["Одно приложение, две роли", "Мини-приложение подключено к чат-боту и не живёт отдельно. Роль задаёт стартовый параметр: без него — кабинет владельца, со слагом сервиса — страница для клиента."],
  ].forEach(([t, b], i) => {
    const x = M + i * (cw3 + 0.25);
    card(s, { x, y: 4.72, w: cw3, h: 2.05, dark: true });
    cardText(s, { x, y: 4.72, w: cw3, h: 2.05, title: t, body: b, dark: true, size: 11, titleSize: 13.5 });
  });
  chrome(s, 13, true);
}

// ====================================================================================
// 14. Reliability & security
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Техническая оценка · надёжность и безопасность", "Стабильность, безопасность, проверяемость");

  const cw = (CW - 0.25 * 2) / 3;
  [
    ["Стабильность и ошибки", [
      "Вебхук всегда отвечает 200 и не вызывает модель — MAX не повторяет доставку",
      "Запрос к MAX API повторяется, только если не дошёл: без дублей сообщений",
      "Генерация — до трёх попыток, затем шаблон: сценарий не упирается в тупик",
      "Ошибка в интерфейсе — понятный текст и «Повторить», введённое не теряется",
      "Повторный прогон сценария — новый сервис со своим адресом; после 10 — понятное сообщение, лишние удаляются кнопкой",
    ]],
    ["Безопасность и данные", [
      "Личность в мини-приложении — только из подписи initData по токену бота, срок жизни — час",
      "Вебхук — секрет в пути, в логах маскируется; неверный секрет → 404",
      "Владелец видит и меняет только свои сервисы и заявки",
      "Ввод пользователей экранируется в сообщениях бота; в модель уходят только описание и правки",
      "Секретов в репозитории нет, .env.example без значений",
    ]],
    ["Воспроизводимость и документация", [
      "Все локальные компоненты — одной командой docker compose up --build",
      "Версии зафиксированы: requirements.txt + requirements.lock, package-lock.json",
      "README закрывает пункты задания по порядку, с таблицей проверки и ожидаемыми результатами",
      "84 автотеста: подделка подписи, чужой токен, чужие сервисы, контраст палитры, сквозной сценарий через HTTP",
      "Сборка образов с нуля — около 4 минут (замер), лимит задания — 5",
    ]],
  ].forEach(([t, items], i) => {
    const x = M + i * (cw + 0.25);
    card(s, { x, y: 1.45, w: cw, h: 4.05 });
    s.addText(t, {
      x: x + 0.24, y: 1.64, w: cw - 0.48, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    bullets(s, { x: x + 0.24, y: 2.08, w: cw - 0.48, h: 3.3, size: 11.5, gap: 6, items });
  });

  card(s, { x: M, y: 5.68, w: CW, h: 0.95, tint: true });
  s.addText(
    rich("**Проверено на проде:** витрина из описания собирается реальной моделью за [замер после деплоя]; сообщения бота с баннером и кнопками проходят валидацию MAX API.", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.84, w: CW - 0.56, h: 0.68, valign: "top", fontFace: FONT, fontSize: 12.5, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  chrome(s, 14, false);
}

// ====================================================================================
// 15. MAX capabilities - the platform bonus
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Платформенный бонус · возможности MAX", "Что мы берём у MAX сверх минимума",
    "Минимум задания — чат-бот с подключённым мини-приложением. Всё ниже — сверх него, и каждая возможность работает в основном сценарии от начала до результата.");

  const code = (text) => ({ text, options: { fontFace: MONO, bold: true, fontSize: 13, color: INK } });
  const word = (text) => ({ text, options: { bold: true, fontSize: 14, color: INK } });
  const cw = (CW - 0.25 * 2) / 3;
  [
    [[code("requestContact()")], "Телефон клиента из аккаунта MAX одной кнопкой. Запись без ввода номера — меньше ошибок и шагов в форме."],
    [[code("shareMaxContent()")], "«Поделиться» открывает родной экран MAX «Отправить в чат»: владелец раздаёт ссылку клиентам, не выходя из мессенджера."],
    [[word("Кнопки "), code("open_app"), word(" с "), code("payload")], "Каждое сообщение бота открывает нужный экран: владельцу — заявки, клиенту — его сервис. Диплинк startapp — для ссылки и QR-кода."],
    [[word("Профиль из "), code("initData")], "Имя клиента уже подставлено в форму записи, а владелец входит без регистрации — аккаунт MAX и есть вход."],
    [[code("BackButton"), word(" и подтверждение закрытия")], "Родная кнопка «Назад» в предпросмотре; если закрыть приложение во время сборки, MAX переспросит."],
    [[code("HapticFeedback")], "Тактильный отклик на ключевые действия на телефоне: сборка готова, заявка подтверждена."],
  ].forEach(([title, body], i) => {
    const x = M + (i % 3) * (cw + 0.25);
    const y = 1.98 + Math.floor(i / 3) * 1.78;
    card(s, { x, y, w: cw, h: 1.6 });
    s.addText(title, { x: x + 0.24, y: y + 0.18, w: cw - 0.48, h: 0.32, fontFace: FONT, isTextBox: true, margin: 0 });
    s.addText(body, {
      x: x + 0.24, y: y + 0.58, w: cw - 0.48, h: 0.92, valign: "top",
      fontFace: FONT, fontSize: 11.5, color: INK_2, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0,
    });
  });

  card(s, { x: M, y: 5.6, w: CW, h: 0.95, tint: true });
  s.addText(
    rich("**Мобильная и веб-версия MAX:** одна вёрстка, проверенная от 320 px. Если клиент MAX не поддерживает метод Bridge, приложение мягко обходится без него — например, «Поделиться» превращается в копирование ссылки.", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.76, w: CW - 0.56, h: 0.68, valign: "top", fontFace: FONT, fontSize: 12, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  chrome(s, 15, false);
}

// ====================================================================================
// 16. Data & integrations
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Техническая оценка · данные и интеграции", "Откуда берутся данные и что мы с ними делаем");

  const cw = (CW - 0.25) / 2;
  const x2 = M + cw + 0.25;
  const block = (x, y, h, title, items, o = {}) => {
    card(s, { x, y, w: cw, h, tint: o.tint });
    s.addText(title, {
      x: x + 0.26, y: y + 0.18, w: cw - 0.52, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    bullets(s, { x: x + 0.26, y: y + 0.6, w: cw - 0.52, h: h - 0.75, size: 12, gap: 5, items });
  };
  block(M, 1.45, 2.3, "Данные в решении", [
    "**Описание бизнеса** — вводит сам владелец в мини-приложении",
    "**Профиль и идентификатор MAX** — из подписанных стартовых параметров",
    "**Телефон клиента** — только если клиент сам поделился через MAX или ввёл вручную",
    "**Заявки** — то, что клиент выбрал в сервисе",
  ]);
  block(M, 3.93, 2.15, "Правила работы с моделью", [
    "Модель только структурирует текст владельца: цены и контакты не придумывает — чего нет в описании, остаётся пустым",
    "Результат владелец видит сразу и правит одной фразой",
    "Модель недоступна — сервис собирается шаблоном, сценарий не блокируется",
  ]);
  block(x2, 1.45, 2.55, "Интеграции — все реальные", [
    "**MAX Bot API** — platform-api2.max.ru: вебхук, сообщения, кнопки",
    "**MAX Bridge** — стартовые параметры, контакт, «Поделиться», навигация",
    "**LLM-провайдер** — описание → конфигурация сервиса, с детерминированным фоллбэком",
    "Государственные информационные системы **не используются и не имитируются**",
  ], { tint: true });
  card(s, { x: x2, y: 4.18, w: cw, h: 1.9 });
  cardText(s, {
    x: x2, y: 4.18, w: cw, h: 1.9, title: "Демонстрационные данные", size: 12, titleSize: 14.5,
    body: "«Автосервис на Лесной», витрины со слайда 9 и заявки на слайдах 7–10 — демо: не реальные организации и клиенты. Экраны сняты с интерфейса мини-приложения на демо-данных.",
  });
  chrome(s, 16, false);
}

// ====================================================================================
// 17. Niches (dark) - breadth of the mechanism, honest about today's scope
// ====================================================================================
{
  const s = darkSlide();
  head(s, "Потенциал масштабирования · ниши", "Одна механика — много ниш",
    "Сейчас AIRuntime в MAX делает ровно два сценария — онлайн-запись и онлайн-заказ, плюс простую заявку. Для ниши меняется только описание: движок, дизайн и маршрут заявки те же.", true);
  const cw = (CW - 0.4) / 3;
  [
    ["Работает сейчас", GOOD, "Запись на время", true, "барбершоп и салон · маникюр и брови · автосервис и шиномонтаж · репетитор · йога и фитнес-студия · массаж · фотограф · груминг и ветеринар · психолог"],
    ["Работает сейчас", GOOD, "Заказ и заявка", true, "кофейня и пекарня · кондитер на заказ · цветы · домашняя еда и ланчи · фермерские продукты · ремонт и отделка · клининг · юрист и бухгалтер · праздники и кейтеринг"],
    ["Не в MVP — следующие типы", HOT, "Что потребует доработки ядра", false, "аренда по суткам с залогом · билеты на события · абонементы и пакеты · предоплата онлайн · доставка с оплатой · расписание сотрудников"],
  ].forEach(([tag, tagColor, title, accent, list], i) => {
    const x = M + i * (cw + 0.2);
    card(s, { x, y: 1.95, w: cw, h: 2.95, dark: true, accent });
    s.addText(tag.toUpperCase(), {
      x: x + 0.26, y: 2.12, w: cw - 0.52, h: 0.24, fontFace: FONT, fontSize: 9.5, bold: true,
      color: tagColor === GOOD ? "6FE0A6" : PINK, charSpacing: 1, isTextBox: true, margin: 0,
    });
    s.addText(title, {
      x: x + 0.26, y: 2.42, w: cw - 0.52, h: 0.32, fontFace: FONT, fontSize: 15, bold: true, color: WHITE, isTextBox: true, margin: 0,
    });
    s.addText(list, {
      x: x + 0.26, y: 2.86, w: cw - 0.52, h: 1.9, valign: "top", fontFace: FONT, fontSize: 12.5,
      color: ON_DARK, lineSpacingMultiple: 1.3, isTextBox: true, margin: 0,
    });
  });
  card(s, { x: M, y: 5.12, w: CW, h: 1.15, dark: true });
  s.addText(
    rich("**Честная граница:** сегодня продукт доводит клиента до записи, заказа или заявки и возвращает владельцу решение в чат. Оплату, склад и график сотрудников не делает — это следующие типы сервиса, а не обещание MVP (слайд 12). Порядок, в котором добавляем ниши, — на слайдах 18–19.", { color: ON_DARK_2 }, WHITE),
    { x: M + 0.28, y: 5.28, w: CW - 0.56, h: 0.85, valign: "top", fontFace: FONT, fontSize: 12.5, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );
  chrome(s, 17, true);
}

// ====================================================================================
// 18. Scaling: core and variable part (dark)
// ====================================================================================
{
  const s = darkSlide();
  head(s, "Потенциал масштабирования · ядро и переменная часть", "Что переносится без изменений, а что адаптируется",
    "Решение тиражируется не потому, что перечислено много регионов, а потому, что понятно, какая часть меняется.", true);

  const cw = (CW - 0.3) / 2;
  [
    ["Ядро — переносится без изменений", ACCENT_ON_DARK, true, [
      "Механика «одно сообщение → работающий сервис в MAX»",
      "Схема сервиса: позиции, цены, длительность, время, поля заявки",
      "Маршрут заявки: клиент → чат владельца → ответ клиенту",
      "Модель доверия и проверка подписи стартовых параметров",
      "Мультитенантный рендер: новый сервис не требует деплоя",
    ]],
    ["Переменная часть — адаптируется под контекст", PINK, false, [
      "Тип сервиса: запись / меню / заявка — **три типа уже есть**",
      "Словарь отрасли в подсказках модели: услуга, позиция, объект",
      "Правила времени: длительность, часы работы, праздники региона",
      "Роли участников: мастер → сотрудники → филиалы",
      "Интеграции: календарь, складские остатки, оплата",
    ]],
  ].forEach(([t, colour, accent, items], i) => {
    const x = M + i * (cw + 0.3);
    card(s, { x, y: 1.9, w: cw, h: 2.3, dark: true, accent });
    s.addText(t, {
      x: x + 0.26, y: 2.1, w: cw - 0.52, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: colour, isTextBox: true, margin: 0,
    });
    bullets(s, { x: x + 0.26, y: 2.52, w: cw - 0.52, h: 2.0, dark: true, size: 12, gap: 5, items });
  });

  card(s, { x: M, y: 4.42, w: CW, h: 1.22, dark: true });
  s.addText(
    rich("**Та же проблема — «клиент хочет записаться или заказать, а владелец отвечает вручную»** — есть в каждой нише со слайда 17. Каждое направление использует готовый тип сервиса и меняет только переменную часть. Порядок — на следующем слайде.", { color: ON_DARK_2 }, WHITE),
    { x: M + 0.28, y: 4.6, w: CW - 0.56, h: 0.92, valign: "top", fontFace: FONT, fontSize: 12.5, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0 }
  );
  chrome(s, 18, true);
}

// ====================================================================================
// 19. Scaling: replication order
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Потенциал масштабирования · порядок тиражирования", "Как AIRuntime переходит из пилота дальше");

  const cw = (CW - 0.16 * 3) / 4;
  [
    ["1", "Пилот", "Санкт-Петербург: 5–10 владельцев микробизнеса услуг с записью на время", [
      ["Адаптируем", "ничего — ядро как есть"],
      ["Ресурсы", "команда, домен с HTTPS, ключ LLM"],
      ["Риск", "владельцы не захотят менять привычку — отбираем тех, кто сам жалуется на переписку"],
      ["Дальше, если", "выполнен критерий успеха (слайд 11)"],
    ]],
    ["2", "Соседние ниши", "Тот же город: кафе, пекарни, кондитеры, цветы", [
      ["Адаптируем", "тип «меню» уже есть; словарь и подсказки модели под общепит"],
      ["Ресурсы", "те же; ограничиваемся предзаказом и самовывозом"],
      ["Риск", "заказ без оплаты не доходит до денег — меряем долю забранных заказов"],
      ["Дальше, если", "доля доведённых заказов не ниже, чем у записи"],
    ]],
    ["3", "Сети услуг", "Другие районы и города через сети барбершопов, автосервисов, студий", [
      ["Адаптируем", "роли «сеть → филиал → мастер», сервис на филиал — первая доработка ядра"],
      ["Ресурсы", "разработка ролей, сопровождение владельцев сетей"],
      ["Риск", "решает головной офис — длинный цикл продаж"],
      ["Дальше, если", "сеть переводит на AIRuntime все свои точки"],
    ]],
    ["4", "Регионы", "Ленинградская область, затем другие регионы — через центры «Мой бизнес» и МСП.РФ", [
      ["Адаптируем", "правила времени и праздники региона; модерация сервисов"],
      ["Ресурсы", "партнёрство, обучение консультантов, бюджет на модель"],
      ["Риск", "качество генерации в новых отраслях, лимит MAX API — 30 запросов в секунду"],
      ["Механика", "консультант собирает сервис вместе с предпринимателем на приёме"],
    ]],
  ].forEach(([n, title, where, facts], i) => {
    const x = M + i * (cw + 0.16);
    card(s, { x, y: 1.42, w: cw, h: 4.1, tint: i === 0 });
    stepMark(s, x + 0.2, 1.6, n);
    s.addText(title, {
      x: x + 0.56, y: 1.58, w: cw - 0.76, h: 0.3, fontFace: FONT, fontSize: 14.5, bold: true, color: INK, isTextBox: true, margin: 0,
    });
    s.addText(where, {
      x: x + 0.2, y: 2.0, w: cw - 0.4, h: 0.75, valign: "top",
      fontFace: FONT, fontSize: 11.5, bold: true, color: INK, lineSpacingMultiple: 1.08, isTextBox: true, margin: 0,
    });
    const runs = [];
    facts.forEach(([label, text], k) => {
      runs.push({ text: label.toUpperCase(), options: { fontSize: 8.5, bold: true, color: INK_3, charSpacing: 1, breakLine: true, paraSpaceBefore: k ? 6 : 0 } });
      runs.push({ text, options: { fontSize: 10.5, color: INK_2, breakLine: k !== facts.length - 1 } });
    });
    s.addText(runs, {
      x: x + 0.2, y: 2.8, w: cw - 0.4, h: 2.65, valign: "top", fontFace: FONT, lineSpacingMultiple: 1.06, isTextBox: true, margin: 0,
    });
  });

  card(s, { x: M, y: 5.7, w: CW, h: 0.78, tint: true });
  s.addText(
    rich("**Горизонт:** та же механика «одно сообщение → работающий сервис в MAX» подходит не только бизнесу — продуктовые команды проверяют гипотезы без разработки, люди делают небольшие сервисы для себя. Это и есть ядро AIRuntime, а запись для микробизнеса — первый вертикальный сценарий на нём.", { color: INK_2 }, INK),
    { x: M + 0.26, y: 5.8, w: CW - 0.52, h: 0.6, valign: "middle", fontFace: FONT, fontSize: 11.5, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0 }
  );
  chrome(s, 19, false);
}

// ====================================================================================
// 20. Pilot
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Сценарий пилотного запуска", "Первый ограниченный запуск");

  const cw = (CW - 0.2 * 2) / 3;
  [
    ["Где и для кого", "Санкт-Петербург. 5–10 владельцев микробизнеса услуг с записью на время — автосервис, барбершоп, мастер маникюра, — которые сейчас ведут запись в личных сообщениях."],
    ["Почему так", "Узкий сегмент, где владелец решает сам и сразу, а запись — ежедневная рутина. Команда встречается с каждым лично, собирает витрину вместе с ним и снимает базовую линию."],
    ["Как встроится в процесс", "AIRuntime заменяет пересказ прайса и согласование времени. Приём, оплата и сама услуга остаются как были."],
    ["Кто участвует", "Владелец создаёт сервис и подтверждает записи, клиенты записываются, команда сопровождает и снимает метрики. Внешние владельцы процесса не нужны."],
    ["Каналы привлечения", "Владельцев — личные встречи, знакомые мастерские и студии, чаты предпринимателей. Клиентов — ссылкой в переписке и QR-кодом на стойке."],
    ["Что понадобится и сроки", "Профиль на «MAX для партнёров», домен с HTTPS, ключ LLM. Неделя 0 — отбор и базовая линия, недели 1–3 — пилот, неделя 4 — решение."],
  ].forEach(([t, b], i) => {
    const x = M + (i % 3) * (cw + 0.2);
    const y = 1.45 + Math.floor(i / 3) * 1.95;
    card(s, { x, y, w: cw, h: 1.78, tint: i === 0 });
    cardText(s, { x, y, w: cw, h: 1.78, title: t, body: b, size: 12, titleSize: 14.5 });
  });

  card(s, { x: M, y: 5.45, w: CW, h: 1.05, tint: true });
  s.addText(
    rich("**Метрики пилота** — доля доведённых записей, время до подтверждения, доля заявок вне рабочих часов, ручных сообщений на запись (слайд 11). **Следующий шаг** при успехе — соседние ниши того же города на готовом типе «меню» (слайд 19).", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.62, w: CW - 0.56, h: 0.75, valign: "top", fontFace: FONT, fontSize: 12.5, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  chrome(s, 20, false);
}

// ====================================================================================
// 21. Limits, assumptions, risks
// ====================================================================================
{
  const s = lightSlide();
  head(s, "Ограничения, риски, допущения", "Что мы знаем и чего пока не знаем",
    "Разделяем подтверждённое источником или замером и то, что остаётся нашей гипотезой.");

  const cw = (CW - 0.25 * 2) / 3;
  [
    ["Ограничения MVP", PILL.blue, [
      "Время — текстовые варианты, пересечения записей не проверяются",
      "Один владелец на сервис, до 10 сервисов",
      "Ответ клиенту доставляется, если MAX разрешает боту написать этому пользователю",
      "Работает там, где клиенты бизнеса уже пользуются MAX",
      "Границы объёма — слайд 12",
    ]],
    ["Допущения — требуют проверки", PILL.guess, [
      "Заметная доля микросервисов ведёт запись вручную и считает это проблемой",
      "Владелец опишет бизнес одним сообщением, а не бросит на полпути",
      "Клиенту привычнее записаться в MAX, чем написать в личные сообщения",
      "Оценки стоимости и сроков альтернатив мы не подтверждали и в расчёт эффекта не берём",
    ]],
    ["Риски и что с ними делаем", PILL.guess, [
      "**Качество генерации** — модель может неверно разобрать описание: правка одной фразой и шаблон",
      "**Доступность модели** — сервис собирается шаблоном, сценарий не блокируется",
      "**Модерация и доверие** — при росте нужна проверка содержимого сервисов",
    ]],
  ].forEach(([t, colours, items], i) => {
    const x = M + i * (cw + 0.25);
    card(s, { x, y: 1.85, w: cw, h: 3.55 });
    pill(s, { x: x + 0.24, y: 2.05, text: t, ...colours });
    bullets(s, { x: x + 0.24, y: 2.5, w: cw - 0.48, h: 2.8, size: 12, gap: 6, items });
  });

  card(s, { x: M, y: 5.58, w: CW, h: 1.02, tint: true });
  s.addText(
    rich("**Что уже подтверждено:** сквозной путь «описание → сервис → запись → заявка владельцу → ответ клиенту» проходит на проде целиком и покрыт 84 автотестами — включая подделку подписи, чужой токен, просроченные параметры и запись на услугу, которой в сервисе нет.", { color: INK_2 }, INK),
    { x: M + 0.28, y: 5.74, w: CW - 0.56, h: 0.75, valign: "top", fontFace: FONT, fontSize: 12.5, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  chrome(s, 21, false);
}

// ====================================================================================
// 22. Sources (dark)
// ====================================================================================
{
  const s = darkSlide();
  head(s, "Источники", "На что мы опирались", null, true);

  card(s, { x: M, y: 1.55, w: CW, h: 3.1, dark: true });
  bullets(s, {
    x: M + 0.3, y: 1.8, w: CW - 0.6, h: 2.7, dark: true, size: 13, gap: 9,
    items: [
      "**ФНС России** — Единый реестр субъектов МСП, июль 2026: 6,6 млн субъектов, 81,8% всех действующих юрлиц и ИП, рост 3,5% год к году",
      "**MAX для разработчиков** — API ботов: методы, вебхуки, кнопки, лимиты запросов",
      "**OpenAPI-спецификация MAX** — github.com/max-messenger/api-schema: форма кнопок и сообщений сверена по ней",
      "**MAX Bridge** и **валидация данных** — стартовые параметры, контакт, «Поделиться», проверка подписи",
      "**Минцифры России** — корневой сертификат для доступа к API платформы",
    ],
  });

  card(s, { x: M, y: 4.9, w: CW, h: 0.95, dark: true, accent: true });
  s.addText(
    "Документация MAX развивается: перед сдачей состав API и методов сверялся с актуальными разделами «API ботов», «MAX Bridge» и «Валидация данных» и с официальной спецификацией, а не с примерами из сторонних источников.",
    { x: M + 0.28, y: 5.05, w: CW - 0.56, h: 0.68, valign: "top", fontFace: FONT, fontSize: 11.5, color: ON_DARK_2, lineSpacingMultiple: 1.12, isTextBox: true, margin: 0 }
  );
  chrome(s, 22, true);
}

// ====================================================================================
// 23. Closing - bookends the cover on the same background
// ====================================================================================
// Its one job is to hand the jury a way to try the product in the next ten seconds, hence
// the QR code rather than a list of URLs to retype. Boxes are deck.html's.
{
  const s = coverSlide();
  const small = { fontFace: FONT, fontSize: 8.6, bold: true, charSpacing: 2, isTextBox: true, margin: 0 };

  lockup(s, px(130));
  s.addText("СПАСИБО ЗА ВНИМАНИЕ", { ...small, x: px(72), y: px(194), w: px(520), h: px(20), color: ACCENT_ON_DARK });
  s.addText("Одно сообщение —\nи запись уже в MAX", {
    x: px(70), y: px(222), w: px(740), h: px(140), valign: "top",
    fontFace: FONT, fontSize: 46, bold: true, color: WHITE, lineSpacingMultiple: 0.98, isTextBox: true, margin: 0,
  });
  s.addText(
    "Бот и мини-приложение работают прямо сейчас. Опишите бизнес одним сообщением — у клиентов будет витрина со своим дизайном и записью, а у вас заявки в чате.",
    {
      x: px(72), y: px(376), w: px(580), h: px(86), valign: "top",
      fontFace: FONT, fontSize: 14.5, color: ON_COVER_LEAD, lineSpacingMultiple: 1.15, isTextBox: true, margin: 0,
    }
  );

  [
    ["БОТ В MAX", "max.ru/t403_hakaton_max_bot"],
    ["МИНИ-ПРИЛОЖЕНИЕ", "airuntime.ru/max"],
    ["ИСХОДНЫЙ КОД", "github.com/airuntime-ru/airuntime-max"],
  ].forEach(([label, value], i) => {
    const y = px(499 + i * 34);
    s.addText(label, { ...small, x: px(72), y, w: px(180), h: px(24), valign: "middle", color: ON_COVER_MUTED });
    s.addText(value, {
      x: px(258), y, w: px(420), h: px(24), valign: "middle",
      fontFace: FONT, fontSize: 13, bold: true, color: WHITE, isTextBox: true, margin: 0,
    });
  });

  // The QR card: dark modules on white, the only way a code scans reliably.
  s.addShape(pres.ShapeType.roundRect, {
    x: px(862), y: px(160), w: px(324), h: px(384), rectRadius: px(28),
    fill: { color: WHITE }, line: { color: WHITE, width: 0 },
    shadow: { type: "outer", color: "000000", blur: 40, offset: 14, angle: 90, opacity: 0.55 },
  });
  s.addImage({ path: "qr-bot.png", x: px(906), y: px(190), w: px(236), h: px(236) });
  s.addText("Наведите камеру", {
    x: px(892), y: px(444), w: px(264), h: px(28), align: "center", valign: "middle",
    fontFace: FONT, fontSize: 15, bold: true, color: INK, isTextBox: true, margin: 0,
  });
  s.addText("Откроется бот в MAX — опишите свой бизнес и получите свою витрину", {
    x: px(892), y: px(476), w: px(264), h: px(44), align: "center", valign: "top",
    fontFace: FONT, fontSize: 11, color: INK_2, lineSpacingMultiple: 1.1, isTextBox: true, margin: 0,
  });

  chrome(s, 23, true);
}

/**
 * pptxgenjs repeats a paragraph's <a:pPr> in front of every run after the first, which the
 * schema does not allow (one pPr, first). PowerPoint forgives it today; strip the repeats
 * so the file is valid rather than tolerated. Compressing also keeps the deck well under
 * the repository's 1000 KB limit - pptxgenjs stores slide XML uncompressed.
 */
async function save(fileName) {
  const zip = await JSZip.loadAsync(await pres.write({ outputType: "nodebuffer" }));
  for (const name of Object.keys(zip.files).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))) {
    const xml = await zip.file(name).async("string");
    zip.file(name, xml.replace(/(<\/a:r>|<a:br\/>)<a:pPr\b[^>]*?(?:\/>|>[\s\S]*?<\/a:pPr>)/g, "$1"));
  }
  fs.writeFileSync(fileName, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
  console.log(`wrote ${fileName}`);
}

save("presentation.pptx");
