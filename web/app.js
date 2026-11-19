/* Yamal Scout front end: dashboard panels + streaming chat. No build step, plain JS. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const STORE_KEY = "yamal-scout-chat-v1";
const COMP_LABEL = { league: "LaLiga", champions_league: "Champions", domestic_cup: "Copa", national_team: "Selección" };
const RESULT_LABEL = { W: "V", D: "E", L: "D" };
const RESULT_WORD = { W: "Victoria", D: "Empate", L: "Derrota" };
const TOOL_LABEL = {
  query_stats: "📊 Consultando estadísticas en BigQuery",
  predict_stat: "🔮 Corriendo el modelo de predicción",
  get_next_matches: "📅 Revisando el calendario",
  get_recent_news: "📰 Leyendo las últimas noticias",
  web_search: "🌐 Buscando en la web",
};
const STAT_LABEL = { shots_on_target: "Tiros al arco", shots: "Tiros", goals: "Goles", assists: "Asistencias" };
const SUGGESTIONS = [
  "¿Cuántos tiros al arco crees que haga Lamine en su próximo partido?",
  "¿Qué probabilidad hay de que marque en su próximo partido?",
  "Compara sus números de esta temporada con la anterior",
  "¿Qué se dice de Lamine esta semana?",
];

/* ---------------- helpers ---------------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(c));
  return node;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p) => `${Math.round(p * 100)}%`;
const num = (x, d = 2) => Number(x).toLocaleString("es-ES", { maximumFractionDigits: d });
const crestUrl = (id) => `https://a.espncdn.com/i/teamlogos/soccer/500/${id}.png`;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function crest(id, name, cls = "") {
  const img = el("img", { src: crestUrl(id), alt: `Escudo de ${name}`, loading: "lazy", class: cls });
  img.addEventListener("error", () => img.replaceWith(el("div", { class: "crest-fallback", text: (name || "?").slice(0, 3).toUpperCase() })));
  return img;
}

function fmtDate(iso, opts) { return new Intl.DateTimeFormat("es-ES", opts).format(new Date(iso)); }

function timeAgo(dateStr) {
  const mins = Math.round((Date.now() - new Date(dateStr)) / 60000);
  if (!isFinite(mins)) return "";
  if (mins < 60) return `hace ${mins} min`;
  if (mins < 1440) return `hace ${Math.round(mins / 60)} h`;
  return `hace ${Math.round(mins / 1440)} d`;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

function countUp(node, target) {
  if (reduceMotion) { node.textContent = target; return; }
  const start = performance.now(), dur = 900;
  const tick = (t) => {
    const k = Math.min(1, (t - start) / dur);
    node.textContent = Math.round(target * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* ---------------- tooltip ---------------- */
const tip = $("#tooltip");
function bindTip(node, html) {
  const move = (e) => {
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = Math.max(8, e.clientY - tip.offsetHeight - 12);
    tip.style.left = `${x}px`; tip.style.top = `${y}px`;
  };
  node.addEventListener("pointerenter", (e) => { tip.innerHTML = html; tip.hidden = false; move(e); });
  node.addEventListener("pointermove", move);
  node.addEventListener("pointerleave", () => { tip.hidden = true; });
}

/* ---------------- bar chart ----------------
   items: [{axis, value, label?, tip, top?, marker?}] - one series, one hue, baseline-anchored. */
function barChart(items, { max, axisTitle, crests } = {}) {
  const peak = max ?? Math.max(...items.map((d) => d.value), 1e-9);
  const bars = el("div", { class: "bars" });
  const cols = items.map((d) => {
    const bar = el("div", { class: "bar" });
    const col = el("div", { class: `bar-col${d.top ? " top" : ""}`, "aria-label": d.aria ?? `${d.axis}: ${d.value}` },
      d.marker ? el("span", { class: "marker", text: d.marker }) : null,
      d.label ? el("span", { class: "bar-label", text: d.label }) : null,
      bar);
    bindTip(col, d.tip);
    bars.append(col);
    return { bar, d };
  });
  const frag = el("div", {}, bars);
  if (crests) frag.append(el("div", { class: "crest-axis" }, crests.map((c) => el("span", {}, c))));
  else frag.append(el("div", { class: "axis" }, items.map((d) => el("span", { text: d.axis }))));
  if (axisTitle) frag.append(el("div", { class: "axis-title", text: axisTitle }));
  // grow from the baseline on the next frame
  requestAnimationFrame(() => requestAnimationFrame(() => {
    for (const { bar, d } of cols) {
      const h = (d.value / peak) * 100;
      bar.style.height = `${d.value > 0 ? Math.max(h, 1.5) : 0}%`;
      const col = bar.parentElement;
      for (const lab of col.querySelectorAll(".bar-label, .marker")) lab.style.bottom = `${h}%`;
    }
  }));
  return frag;
}

function distributionItems(stat) {
  const keys = ["0", "1", "2", "3", "4"];
  const vals = keys.map((k) => stat.p_exact[k] ?? 0);
  vals.push(stat.p_at_least[">=5"] ?? 0);
  const top = Math.min(stat.most_likely_value, 5);
  return vals.map((v, i) => ({
    axis: i === 5 ? "5+" : String(i),
    value: v,
    top: i === top,
    label: i === top ? pct(v) : null,
    aria: `${i === 5 ? "5 o más" : i}: ${pct(v)}`,
    tip: `<b>${pct(v)}</b> de probabilidad de ${i === 5 ? "5 o más" : `exactamente ${i}`}`,
  }));
}

/* ---------------- dashboard ---------------- */
async function loadOverview() {
  const data = await getJSON("/api/overview");
  $("#season-label").textContent = `Temporada ${data.season.label} · ${data.season.team} · solo partidos oficiales`;
  for (const node of document.querySelectorAll("#season-tiles .tile-value")) countUp(node, data.season[node.dataset.key]);
  const c = data.career;
  $("#career-line").textContent = `Carrera: ${c.matches} partidos · ${c.goals} goles · ${c.assists} asistencias · ${c.shots_on_target} tiros al arco`;

  const recent = [...data.recent].reverse(); // oldest -> newest, left to right
  $("#results-strip").replaceChildren(...recent.map((m) => {
    const chip = el("span", { class: `res ${m.result}`, text: RESULT_LABEL[m.result] ?? m.result });
    bindTip(chip, `<b>${esc(RESULT_WORD[m.result] ?? m.result)}</b> ${esc(m.score)}<br>vs ${esc(m.opponent)}`);
    return chip;
  }));
  const items = recent.map((m) => ({
    axis: m.opponent,
    value: m.shots_on_target,
    marker: m.goals ? "⚽".repeat(Math.min(m.goals, 3)) : null,
    aria: `${m.opponent}: ${m.shots_on_target} tiros al arco, ${m.goals} goles`,
    tip: `<b>${m.home ? "vs" : "en casa de"} ${esc(m.opponent)}</b><br>${fmtDate(m.date, { day: "numeric", month: "short" })} · ${esc(m.competition)}<br>`
      + `${esc(RESULT_WORD[m.result] ?? "")} ${esc(m.score)}<br>`
      + `Tiros al arco: <b>${m.shots_on_target}</b> · Tiros: ${m.shots}<br>Goles: ${m.goals} · Asistencias: ${m.assists}`,
  }));
  const peak = Math.max(4, ...items.map((d) => d.value));
  $("#form-chart").replaceChildren(barChart(items, {
    max: peak, crests: recent.map((m) => crest(m.opponent_id, m.opponent)),
    axisTitle: "⚽ = partido con gol · pasa el cursor por cada barra",
  }));
}

let countdownTimer;
async function loadFixtures() {
  const data = await getJSON("/api/fixtures");
  const [next, ...later] = data.upcoming ?? [];
  if (!next) { $("#kickoff").textContent = "Sin partidos programados"; return; }
  const us = { id: next.team_id, name: next.team };
  const them = { id: next.opponent_id, name: next.opponent };
  const [home, away] = next.home ? [us, them] : [them, us];
  for (const [slot, t] of [["#team-home", home], ["#team-away", away]]) {
    $(slot).replaceChildren(el("div", { class: "crest" }, crest(t.id, t.name)), el("span", { text: t.name }));
  }
  $("#match-comp").textContent = next.competition || COMP_LABEL[next.competition_type] || "Partido";
  $("#kickoff").textContent = `${fmtDate(next.date_utc, { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}${next.venue ? " · " + next.venue : ""}`;
  $("#ask-forecast").dataset.question =
    `¿Cuántos tiros al arco y qué probabilidad de gol tiene Lamine contra ${next.opponent}? Dame tu nivel de confianza.`;

  const cd = $("#countdown");
  const kick = new Date(next.date_utc).getTime();
  clearInterval(countdownTimer);
  const tick = () => {
    let s = Math.floor((kick - Date.now()) / 1000);
    if (s <= 0) {
      cd.classList.add("live");
      cd.replaceChildren(el("div", {}, el("b", { text: s > -2 * 3600 ? "¡EN JUEGO!" : "FINAL" })));
      clearInterval(countdownTimer);
      return;
    }
    const parts = [Math.floor(s / 86400), Math.floor((s % 86400) / 3600), Math.floor((s % 3600) / 60), s % 60];
    cd.querySelectorAll("b").forEach((b, i) => { b.textContent = String(parts[i]).padStart(2, "0"); });
  };
  tick();
  countdownTimer = setInterval(tick, 1000);

  $("#later-matches").replaceChildren(...later.slice(0, 3).map((m) => el("li", {},
    crest(m.opponent_id, m.opponent),
    el("span", { text: `${m.team} ${m.home ? "vs" : "@"} ${m.opponent}` }),
    el("span", { class: "when", text: fmtDate(m.date_utc, { day: "numeric", month: "short" }) }))));
}

let forecastData, currentStat = "shots_on_target";
async function loadForecast() {
  forecastData = await getJSON("/api/forecast");
  const ctx = forecastData.context;
  $("#forecast-context").textContent = `${ctx.home ? "Local" : "Visitante"} · ${COMP_LABEL[ctx.competition_type] ?? ctx.competition_type}`;
  $("#stat-tabs").replaceChildren(...Object.keys(forecastData.stats).map((key) => el("button", {
    class: "tab", role: "tab", "aria-selected": String(key === currentStat), text: STAT_LABEL[key] ?? key,
    onclick: () => { currentStat = key; renderForecast(); },
  })));
  renderForecast();
}

function renderForecast() {
  for (const b of document.querySelectorAll("#stat-tabs .tab")) b.setAttribute("aria-selected", String(b.textContent === STAT_LABEL[currentStat]));
  const s = forecastData.stats[currentStat];
  const bt = s.backtest;
  const [lo, hi] = s.interval_80pct;
  const headline = el("div", { class: "headline" },
    el("span", { class: "sub", text: "Lo más probable" }),
    el("span", { class: "big" }, String(s.most_likely_value), el("small", { text: s.label.toLowerCase() })),
    el("span", { class: "sub", text: `Media esperada ${num(s.expected_value)} · 80% de las veces entre ${lo} y ${hi}` }),
    el("span", { class: "pge1" }, "Al menos 1: ", el("b", { text: pct(s.p_at_least[">=1"]) })),
    el("div", { class: "badges" },
      el("span", { class: `badge ${bt.beats_baseline ? "good" : "bad"}`, text: bt.beats_baseline ? "✓ Le gana al promedio histórico" : "✗ No le gana al promedio" }),
      el("span", { class: "badge", text: `Acierto del rango 80%: ${pct(bt.coverage_80pct)}` }),
      el("span", { class: "badge", text: `Error medio ${num(bt.mae)}` })));
  const chart = barChart(distributionItems(s), { axisTitle: `${s.label} en el próximo partido` });
  $("#forecast-body").replaceChildren(headline, chart);
}

async function loadNews() {
  const data = await getJSON("/api/news");
  const items = data.articles ?? [];
  if (!items.length) { $("#news-list").replaceChildren(el("li", { class: "muted", text: "Sin noticias recientes." })); $("#ticker").textContent = ""; return; }
  $("#news-list").replaceChildren(...items.slice(0, 7).map((a) => el("li", {},
    el("a", { href: a.link, target: "_blank", rel: "noopener noreferrer" }, a.title,
      el("small", { text: [a.source, timeAgo(a.published)].filter(Boolean).join(" · ") })))));
  $("#ticker").replaceChildren(...items.slice(0, 10).map((a) =>
    el("a", { href: a.link, target: "_blank", rel: "noopener noreferrer", text: a.title.replace(/ - [^-]+$/, "") })));
}

function loadAll() {
  const jobs = [
    loadOverview().catch((e) => fail("#form-chart", e)),
    loadFixtures().catch((e) => fail("#kickoff", e)),
    loadForecast().catch((e) => fail("#forecast-body", e)),
    loadNews().catch((e) => fail("#news-list", e)),
  ];
  return Promise.all(jobs);
}
function fail(sel, err) {
  console.error(err);
  $(sel).replaceChildren(el("p", { class: "error-text", text: "No se pudo cargar. Revisa el servidor." }));
}

$("#refresh-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.classList.add("spinning");
  try { await fetch("/api/refresh", { method: "POST" }); await loadAll(); } finally { btn.classList.remove("spinning"); }
});

/* ---------------- chat ---------------- */
const messagesBox = $("#messages");
const input = $("#input");
const sendBtn = $("#send");
let state = loadState();
let busy = false;

marked.setOptions({ breaks: true, gfm: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
});
const md = (text) => DOMPurify.sanitize(marked.parse(text || ""));

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved?.sessionId) return saved;
  } catch { /* storage unavailable or corrupt: start fresh */ }
  return { sessionId: crypto.randomUUID(), items: [] };
}
function saveState() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* private mode etc. */ }
}

function forecastCard(data) {
  const label = STAT_LABEL[data.target] ?? data.target;
  const ctx = data.context ?? {};
  return el("div", { class: "chat-card" },
    el("h4", {}, `${label} · ${ctx.home ? "local" : "visitante"} · ${COMP_LABEL[ctx.competition_type] ?? ""}`,
      el("span", { text: `≥1: ${pct(data.p_at_least[">=1"])}` })),
    barChart(distributionItems(data), { axisTitle: `Probabilidad de cada resultado (${label.toLowerCase()})` }));
}

function addMessage(role) {
  $("#empty-state")?.remove();
  const bubble = el("div", { class: "bubble" });
  const row = el("div", { class: `msg ${role}` }, el("div", { class: "avatar", text: role === "user" ? "🙋" : "⚽" }), bubble);
  messagesBox.append(row);
  return bubble;
}

function renderStored(item) {
  const bubble = addMessage(item.role);
  if (item.role === "user") { bubble.textContent = item.text; return; }
  if (item.steps?.length) bubble.append(el("div", { class: "steps" }, item.steps.map((s) => el("div", { class: "step done" }, el("span", { class: "spin" }), s))));
  for (const c of item.cards ?? []) bubble.append(forecastCard(c));
  bubble.append(el("div", { class: `answer${item.error ? " error-text" : ""}`, html: null }));
  bubble.lastChild.innerHTML = md(item.text);
}

function scrollDown() { messagesBox.scrollTop = messagesBox.scrollHeight; }

function renderEmpty() {
  messagesBox.replaceChildren(el("div", { class: "empty", id: "empty-state" },
    el("div", { class: "empty-ball", text: "⚽" }),
    el("p", {}, el("b", { text: "¿Qué quieres saber de Lamine?" }), el("br"), "Pregunta por su próximo partido, sus números o lo que se dice de él."),
    el("div", { class: "suggestions" }, SUGGESTIONS.map((q) => el("button", { class: "suggestion", text: q, onclick: () => ask(q) })))));
}

async function ask(question) {
  question = question.trim();
  if (!question || busy) return;
  busy = true; sendBtn.disabled = true;
  showView("chat");

  addMessage("user").textContent = question;
  state.items.push({ role: "user", text: question });
  saveState();

  const bubble = addMessage("bot");
  const steps = el("div", { class: "steps" });
  const thinking = el("div", { class: "thinking" }, el("span", { class: "ball", text: "⚽" }), "Analizando la jugada…");
  const cards = el("div");
  const answer = el("div", { class: "answer streaming" });
  bubble.append(thinking, steps, cards, answer);
  scrollDown();

  const record = { role: "bot", text: "", steps: [], cards: [] };
  const stepNodes = {};
  let text = "", pending = false;
  const paint = () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; answer.innerHTML = md(text); scrollDown(); });
  };

  const handle = (ev) => {
    switch (ev.type) {
      case "thinking":
        thinking.hidden = false; break;
      case "text":
        thinking.hidden = true; text += ev.text; paint(); break;
      case "tool_start": {
        thinking.hidden = false;
        if (text) { text += "\n\n"; } // text before a tool call stays; the answer continues below it
        let label = TOOL_LABEL[ev.name] ?? `🔧 ${ev.name}`;
        if (ev.name === "predict_stat" && ev.input?.target) label += ` · ${STAT_LABEL[ev.input.target] ?? ev.input.target}`;
        const node = el("div", { class: "step" }, el("span", { class: "spin" }), label);
        stepNodes[ev.id] = node; steps.append(node); record.steps.push(label); scrollDown();
        break;
      }
      case "tool_end":
        stepNodes[ev.id]?.classList.add("done");
        if (ev.name === "predict_stat" && ev.data && !ev.data.error) {
          cards.append(forecastCard(ev.data));
          record.cards.push({ target: ev.data.target, context: ev.data.context, p_exact: ev.data.p_exact,
            p_at_least: ev.data.p_at_least, most_likely_value: ev.data.most_likely_value });
          scrollDown();
        }
        break;
      case "error":
        text += `${text ? "\n\n" : ""}⚠️ ${ev.message}`; record.error = true; paint(); break;
    }
  };

  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: question }),
    });
    if (!res.ok) throw new Error(res.status === 409 ? "Todavía estoy respondiendo la pregunta anterior." : `Error del servidor (${res.status}).`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, cut); buf = buf.slice(cut + 2);
        for (const line of chunk.split("\n")) if (line.startsWith("data: ")) handle(JSON.parse(line.slice(6)));
      }
    }
  } catch (err) {
    handle({ type: "error", message: err.message || "Se cortó la conexión." });
  } finally {
    thinking.remove();
    for (const n of Object.values(stepNodes)) n.classList.add("done");
    answer.classList.remove("streaming");
    answer.innerHTML = md(text);
    record.text = text;
    state.items.push(record);
    saveState();
    busy = false; sendBtn.disabled = false;
    scrollDown();
  }
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value;
  input.value = ""; autosize();
  ask(q);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
function autosize() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; }
input.addEventListener("input", autosize);

$("#new-chat").addEventListener("click", async () => {
  if (busy) return;
  try { await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: state.sessionId }) }); } catch { /* server down: still clear locally */ }
  state = { sessionId: crypto.randomUUID(), items: [] };
  saveState();
  renderEmpty();
});

$("#ask-forecast").addEventListener("click", (e) => ask(e.currentTarget.dataset.question || SUGGESTIONS[0]));

/* ---------------- mobile views ---------------- */
function showView(view) {
  $("#layout").dataset.view = view;
  for (const b of document.querySelectorAll(".bottom-nav button")) b.classList.toggle("active", b.dataset.go === view);
  if (view === "chat") setTimeout(scrollDown, 0);
}
for (const b of document.querySelectorAll(".bottom-nav button")) b.addEventListener("click", () => showView(b.dataset.go));

/* ---------------- boot ---------------- */
if (state.items.length) state.items.forEach(renderStored); else renderEmpty();
scrollDown();
loadAll();
