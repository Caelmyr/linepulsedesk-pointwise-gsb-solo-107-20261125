const one = selector => document.querySelector(selector);
const form = one("#settings");
const canvas = one("#capture");
const chart = one("#profile");
const state = {card:null, slot:null, report:null, image:null, selected:null, drag:null, draft:null, load:0,
  correspondence:null, suggestion:null, cross:{a:null,b:null}, previewToken:0};
const numeric = ["x","y","w","h","line_us","exposure_us","trial_hz"];
const fmt = (value, places=1) => value == null ? "—" : Number(value).toFixed(places);
const escape = value => String(value).replace(/[&<>"']/g, char =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
let timer;

async function request(path, method="GET", body) {
  const options = {method};
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers = {"Content-Type":"application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/linepulse" + path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "读数请求失败");
  return data;
}

function message(text) {
  const area = one("#toast");
  area.textContent = text;
  area.hidden = false;
  clearTimeout(timer);
  timer = setTimeout(() => { area.hidden = true; }, 4000);
}

function guard(callback) {
  return event => void Promise.resolve().then(() => callback(event)).catch(error => message(error.message));
}

function base() {
  return "/cards/" + state.card.id;
}

function stamped(path) {
  return path + (path.includes("?") ? "&" : "?") + "revision=" + state.card.revision;
}

function entry() {
  return state.card.photos[state.slot];
}

async function updateCards() {
  const {cards} = await request("/cards");
  const list = one("#cards");
  list.replaceChildren();
  for (const record of cards) {
    const button = document.createElement("button");
    button.className = "card-item" + (record.id === state.card?.id ? " active" : "");
    button.innerHTML = `<strong>${escape(record.name)}</strong><small>${record.synthetic ? "合成练习 · " : ""}${record.photo_count} 张 · r${record.revision}</small>`;
    button.addEventListener("click", guard(async () => {
      await open(await request("/cards/" + record.id));
    }));
    list.append(button);
  }
  return cards;
}

async function open(card) {
  state.card = card;
  state.correspondence = card.correspondence || card.mapping || null;
  state.suggestion = null;
  state.cross = {a:null, b:null};
  one("#empty").hidden = true;
  one("#workspace").hidden = false;
  setupCorrespondencePanel();
  void previewOnOpen();
  await show(card.active);
  await updateCards();
}

async function previewOnOpen() {
  if (!state.card || !state.card.photos?.b || activeCorr()) return;
  const token = ++state.previewToken;
  let limit = Math.trunc(Number(one("#match-limit").value));
  if (!Number.isFinite(limit) || limit < 0) limit = 6;
  try {
    const suggestion = await request(base() + `/correspondence?limit=${limit}`);
    if (token !== state.previewToken || !state.card || activeCorr()) return;
    state.suggestion = suggestion;
    setupCorrespondencePanel();
  } catch {
    /* Non-fatal: the user can press “按内容生成对应” explicitly. */
  }
}

async function show(slot) {
  state.slot = slot;
  state.selected = state.cross?.[slot] ?? null;
  state.report = null;
  state.image = null;
  state.drag = null;
  state.draft = null;
  const load = ++state.load;
  const photo = entry();
  one("#card-title").textContent = state.card.name;
  one("#annotation").textContent = `${state.card.synthetic ? "合成练习 · " : ""}r${state.card.revision} · ${photo.name}`;
  one("#photo-size").textContent = `${photo.width} × ${photo.height} px`;
  one("#point-main").textContent = "点原图或剖面查看实际读出线。拖动原图可重画选区。";
  one("#counterpart-reading").hidden = true;
  one("#counterpart-reading").replaceChildren();
  one("#photo-switch").replaceChildren();
  for (const id of Object.keys(state.card.photos)) {
    const button = document.createElement("button");
    button.textContent = "照片 " + id.toUpperCase();
    button.setAttribute("aria-pressed", String(id === slot));
    button.dataset.slot = id;
    button.addEventListener("click", guard(() => show(id)));
    one("#photo-switch").append(button);
  }
  for (const [key, value] of Object.entries(photo.settings)) form.elements[key].value = value;
  form.elements.name.value = state.card.name;
  const row = `/linepulse${base()}/`;
  one("#csv").href = stamped(row + `export/${slot}/trace.csv`);
  one("#png").href = stamped(row + `export/${slot}/proof.png`);
  one("#zip").href = stamped(row + "export/observation.zip");
  const image = new Image();
  image.src = row + `source/${slot}.png`;
  const [report] = await Promise.all([request(stamped(base() + `/trace/${slot}`)), image.decode()]);
  if (load !== state.load) return;
  state.report = report;
  state.image = image;
  canvas.width = photo.width;
  canvas.height = photo.height;
  canvas.style.maxWidth = Math.min(520, photo.width / photo.height * 520) + "px";
  drawCapture();
  renderReports();
  setupCorrespondencePanel();
  if (state.selected != null) void selectLine(state.selected);
}

function drawCapture() {
  if (!state.image) return;
  const context = canvas.getContext("2d");
  context.drawImage(state.image, 0, 0);
  const config = entry().settings;
  context.strokeStyle = "#7fe3cc";
  context.lineWidth = Math.max(canvas.width, canvas.height) / 240;
  context.strokeRect(config.x, config.y, config.w, config.h);
  if (state.selected != null) {
    context.strokeStyle = "#ffbf7d";
    context.beginPath();
    if (config.axis === "rows") {
      context.moveTo(config.x, state.selected + .5);
      context.lineTo(config.x + config.w, state.selected + .5);
    } else {
      context.moveTo(state.selected + .5, config.y);
      context.lineTo(state.selected + .5, config.y + config.h);
    }
    context.stroke();
  }
  const draft = state.drag?.box || state.draft;
  if (draft) {
    context.save();
    context.setLineDash([6, 4]);
    context.strokeStyle = "#f0f8ff";
    context.strokeRect(draft.x, draft.y, draft.w, draft.h);
    context.restore();
  }
}

function plots(graph, series, domain, label, selected) {
  const bounds = graph.getBoundingClientRect();
  const width = bounds.width, height = bounds.height;
  if (width < 80 || height < 45) return;
  const ratio = window.devicePixelRatio || 1;
  graph.width = Math.round(width * ratio);
  graph.height = Math.round(height * ratio);
  const context = graph.getContext("2d");
  context.scale(ratio, ratio);
  context.fillStyle = "#a9bacb";
  context.strokeStyle = "#26384a";
  context.font = "10px monospace";
  const pad = {left:37, right:13, top:10, bottom:24};
  const right = width - pad.right;
  const bottom = height - pad.bottom;
  const transformX = index => pad.left + index * (right - pad.left) / Math.max(series[0].data.length - 1, 1);
  const transformY = value => bottom - (value - domain[0]) * (bottom - pad.top) / Math.max(.01, domain[1] - domain[0]);
  for (let i = 0; i < 5; i++) {
    const value = domain[0] + (domain[1] - domain[0]) * i / 4;
    const y = transformY(value);
    context.beginPath();
    context.moveTo(pad.left, y); context.lineTo(right, y); context.stroke();
    context.fillText(fmt(value,0),6,y+3);
  }
  context.fillText(label[0],pad.left,height-5);
  context.textAlign = "right"; context.fillText(label[1],right,height-5); context.textAlign = "left";
  if (selected != null) {
    const x = transformX(selected);
    context.strokeStyle = "#d2e2ee";
    context.setLineDash([4,3]);
    context.beginPath();
    context.moveTo(x,pad.top); context.lineTo(x,bottom); context.stroke();
    context.setLineDash([]);
  }
  for (const {data,color} of series) {
    context.strokeStyle = color;
    context.lineWidth = 1.3;
    context.beginPath();
    data.forEach((value,i) => {
      const x = transformX(i), y = transformY(value);
      if (i === 0) context.moveTo(x,y);
      else context.lineTo(x,y);
    });
    context.stroke();
  }
}

function drawGraphs() {
  if (!state.report) return;
  const rows = state.report.lines;
  const measured = rows.map(item => item.measured), fitted = rows.map(item => item.fit);
  const all = [...measured,...fitted];
  const start = rows[0].index, end = rows.at(-1).index;
  const index = state.selected >= start && state.selected <= end && state.selected != null ? state.selected-start : null;
  plots(chart,[{data:measured,color:"#7fe3cc"},{data:fitted,color:"#ffbf7d"}],
    [Math.min(...all)-1,Math.max(...all)+1],[String(start),`${end} ${entry().settings.axis==="rows"?"行":"列"}`],index);
  const spectrum = state.report.spectrum;
  plots(one("#spectrum"),[{data:spectrum.amplitudes,color:"#7fe3cc"}],
    [0,Math.max(1,...spectrum.amplitudes)*1.07],["0",`${fmt(spectrum.nyquist_hz,0)} Hz`]);
}

function renderReports() {
  const {summary, spectrum, fit} = state.report;
  const metrics = [
    ["实测线",fmt(summary.count,0),"条"],["亮度跨度",fmt(summary.max-summary.min),"级"],
    ["最大峰",fmt(spectrum.peaks[0]?.hz,2),"Hz"],["试放偏差",fmt(fit.error,2),"级"],
  ];
  one("#metrics").innerHTML = metrics.map(([title,value,unit]) =>
    `<div class="metric"><small>${title}</small><strong>${value}</strong> <em>${unit}</em></div>`).join("");
  one("#fit-report").textContent = `试放 ${fmt(fit.trial_hz,2)} Hz · 表观 ${fmt(fit.apparent_hz,2)} Hz · 相位 ${fmt(fit.phase_deg)}° · 暗端 ${fmt(100*summary.dark_fraction,2)}% · 亮端 ${fmt(100*summary.white_fraction,2)}%`;
  one("#nyquist").textContent = `上限 ${fmt(spectrum.nyquist_hz,2)} Hz`;
  const area = one("#peaks");
  area.replaceChildren();
  if (!spectrum.peaks.length) area.textContent = "没有高过噪声底的表观峰。";
  for (const peak of spectrum.peaks.slice(0,6)) {
    const node = document.createElement("span");
    node.className = "chip";
    node.textContent = `${fmt(peak.hz,2)} Hz · ${fmt(peak.amplitude,1)} 级`;
    area.append(node);
  }
  drawGraphs();
}

function sourcePoint(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0,Math.min(canvas.width-1,Math.floor((event.clientX-bounds.left)*canvas.width/bounds.width))),
    y: Math.max(0,Math.min(canvas.height-1,Math.floor((event.clientY-bounds.top)*canvas.height/bounds.height))),
  };
}

async function selectLine(index) {
  state.selected = index;
  if (state.cross) state.cross[state.slot] = index;
  const stamp = state.load;
  drawCapture(); drawGraphs(); updateMatchSelection();
  const report = await request(stamped(base()+`/line/${state.slot}?index=${index}`));
  if (stamp !== state.load || index !== state.selected) return;
  if (report.correspondence && !state.correspondence) state.correspondence = report.correspondence;
  const axis = report.axis === "rows" ? "行" : "列";
  const row = report.line;
  one("#point-main").textContent = row
    ? `原图第 ${axis} ${row.index} · t=${fmt(row.time_us,1)} µs · 实测 ${fmt(row.measured,2)} · 试放 ${fmt(row.fit,2)}`
    : `原图第 ${axis} ${index} 在选区外。`;
  renderCounterpart(report, row);
}

function renderCounterpart(report, row) {
  const box = one("#counterpart-reading");
  box.replaceChildren();
  if (!row || !report.correspondence) {
    box.hidden = true;
    return;
  }
  const other = state.slot === "a" ? "b" : "a";
  const otherName = other.toUpperCase();
  if (report.counterpart && report.counterpart.line) {
    const cp = report.counterpart;
    state.cross[other] = cp.index;
    box.append(document.createTextNode(" ｜ "));
    const ok = document.createElement("span");
    ok.className = "cp-ok";
    ok.textContent = `${otherName} 对应原图行 ${cp.index} · 实际亮度 ${fmt(cp.measured, 2)}`;
    box.append(ok);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-btn";
    button.id = "goto-counterpart";
    button.dataset.slot = other;
    button.dataset.index = cp.index;
    button.textContent = `切到 ${otherName} 查看第 ${cp.index} 行`;
    button.addEventListener("click", guard(async () => {
      state.cross[other] = cp.index;
      if (state.slot !== other) await show(other);
      else await selectLine(cp.index);
    }));
    box.append(button);
  } else {
    const missing = document.createElement("span");
    missing.className = "cp-missing";
    missing.dataset.missing = "true";
    missing.textContent = ` ｜ ${otherName} · 缺少对应`;
    box.append(missing);
  }
  box.hidden = false;
}

canvas.addEventListener("pointerdown",event => {
  if (!state.image) return;
  state.drag = {origin:sourcePoint(event),box:null};
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove",event => {
  if (!state.drag) return;
  const first = state.drag.origin, end = sourcePoint(event);
  state.drag.box = {x:Math.min(first.x,end.x),y:Math.min(first.y,end.y),
    w:Math.abs(first.x-end.x)+1,h:Math.abs(first.y-end.y)+1};
  drawCapture();
});
canvas.addEventListener("pointerup",guard(async event => {
  if (!state.drag) return;
  const {box,origin} = state.drag;
  state.drag = null;
  if (box && box.w>=16 && box.h>=16) {
    state.draft = box;
    for (const [key,value] of Object.entries(box)) form.elements[key].value=value;
    drawCapture();
    return;
  }
  const dimension = entry().settings.axis === "rows" ? "y" : "x";
  await selectLine(origin[dimension]);
}));
canvas.addEventListener("pointercancel", () => {state.drag=null;drawCapture();});
chart.addEventListener("click",guard(async event => {
  if (!state.report) return;
  const bounds = chart.getBoundingClientRect();
  const ratio = Math.min(1,Math.max(0,(event.clientX-bounds.left-37)/(bounds.width-50)));
  const rows = state.report.lines;
  await selectLine(rows[Math.round(ratio*(rows.length-1))].index);
}));
window.addEventListener("resize",drawGraphs);

/* ---------- A/B content-derived row correspondence ---------- */

function activeCorr() {
  return state.correspondence || state.suggestion || null;
}

function setupCorrespondencePanel() {
  if (!state.card) return;
  const hasBoth = Boolean(state.card.photos && state.card.photos.a && state.card.photos.b);
  one("#correspondence-panel").hidden = !hasBoth;
  if (!hasBoth) return;
  const corr = activeCorr();
  const input = one("#match-limit");
  if (corr && corr.limit != null && document.activeElement !== input) input.value = corr.limit;
  one("#adopt-matches").disabled = !corr;
  installIdAliases();
  renderMatchLists();
}

function pairAB(pair) {
  if (Array.isArray(pair)) return {a: Number(pair[0]), b: Number(pair[1]), clarity: pair[2]?.clarity};
  const a = pair.a ?? pair.a_index ?? pair.index_a ?? pair.source;
  const b = pair.b ?? pair.b_index ?? pair.index_b ?? pair.target;
  return {a: a == null ? null : Number(a), b: b == null ? null : Number(b), clarity: pair.clarity};
}

function deriveLines(corr, slot) {
  const listKey = slot === "a" ? "lines_a" : "lines_b";
  if (Array.isArray(corr[listKey]) && corr[listKey].length) return corr[listKey];
  const entry = state.card && state.card.photos ? state.card.photos[slot] : null;
  if (!entry) return [];
  const horizontal = entry.settings.axis !== "rows";
  const start = horizontal ? entry.settings.x : entry.settings.y;
  const length = horizontal ? entry.settings.w : entry.settings.h;
  const table = slot === "a" ? corr.a_to_b : corr.b_to_a;
  const pairTable = table || Object.fromEntries((corr.pairs || [])
    .map(pair => {
      const ab = pairAB(pair);
      return [String(ab[slot]), ab[slot === "a" ? "b" : "a"]];
    }));
  return Array.from({length}, (_, offset) => {
    const index = start + offset;
    const paired = pairTable ? pairTable[String(index)] : null;
    return {index, match: paired == null ? null : Number(paired),
      status: paired == null ? "unmatched" : "matched"};
  });
}

function makeMatchRow(line, slot) {
  const otherName = slot === "a" ? "B" : "A";
  const paired = line.match ?? line.paired_index ?? null;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "match-row " + (paired == null ? "missing" : "matched")
    + (line.status ? " status-" + line.status : "");
  button.dataset.slot = slot;
  button.dataset.index = line.index;
  if (paired != null) button.dataset.paired = paired;
  button.setAttribute("aria-label", paired == null
    ? `${slot.toUpperCase()} ${line.index} 缺少对应`
    : `${slot.toUpperCase()} ${line.index} 对应 ${otherName} ${paired}`);
  const index = document.createElement("span");
  index.className = "mr-index";
  index.textContent = `${slot.toUpperCase()} ${line.index}`;
  button.append(index);
  const link = document.createElement("span");
  link.className = "mr-link";
  link.textContent = paired == null ? "缺少对应" : `→ ${otherName} ${paired}`;
  button.append(link);
  if (paired != null && line.clarity != null) {
    const clarity = document.createElement("span");
    clarity.className = "mr-score";
    clarity.textContent = `${Math.round((line.clarity || 0) * 100)}`;
    button.append(clarity);
  }
  button.title = paired == null
    ? `原图${slot.toUpperCase()}第 ${line.index} 行没有可靠对应`
    : `点击查看 ${slot.toUpperCase()} ${line.index} ↔ ${otherName} ${paired}`;
  button.addEventListener("click", guard(async () => {
    state.cross[slot] = line.index;
    if (paired != null) state.cross[slot === "a" ? "b" : "a"] = paired;
    if (state.slot !== slot) await show(slot);
    else await selectLine(line.index);
  }));
  return button;
}

function renderMatchLists() {
  const listA = one("#match-list");
  const listB = one("#match-list-b");
  if (!listA || !listB) return;
  listA.replaceChildren();
  listB.replaceChildren();
  const corr = activeCorr();
  const status = one("#correspondence-status");
  const summary = one("#match-summary");
  const wrapB = one("#match-b-wrap");
  if (!corr) {
    status.textContent = "尚未生成对应";
    summary.textContent = "填好行号差上限后，按两图框内的行内纹样生成一份单调、唯一的按行对应。";
    wrapB.hidden = true;
    return;
  }
  const linesA = deriveLines(corr, "a");
  const linesB = deriveLines(corr, "b");
  for (const line of linesA) listA.append(makeMatchRow(line, "a"));
  for (const line of linesB) listB.append(makeMatchRow(line, "b"));
  const pairs = corr.pairs || [];
  const unmatchedA = linesA.filter(line => line.match == null).length;
  const unmatchedB = linesB.filter(line => line.match == null).length;
  const avgClarity = pairs.length
    ? pairs.reduce((sum, pair) => sum + (pair.clarity ?? 0), 0) / pairs.length : 0;
  status.textContent = `已配 ${pairs.length} 对 · 上限 ${corr.limit ?? "—"}`;
  summary.textContent = `A ${linesA.length} 行中 ${unmatchedA} 行留空，B ${linesB.length} 行中 ${unmatchedB} 行留空`
    + `；平均纹样清楚度 ${Math.round(avgClarity * 100)}%。点任意一行可在原点读核对，未配上的行显示“缺少对应”。`;
  wrapB.hidden = linesB.length === 0;
  updateMatchSelection(false);
}

function updateMatchSelection(scroll = true) {
  document.querySelectorAll(".match-row").forEach(element => {
    const active = element.dataset.slot === state.slot
      && Number(element.dataset.index) === state.selected;
    element.classList.toggle("active", active);
    if (active && scroll) element.scrollIntoView({block:"nearest"});
  });
}

async function generateCorrespondence() {
  if (!state.card || !state.card.photos.b) {
    message("需要 A、B 两张照片才能建立行对应。");
    return;
  }
  const input = one("#match-limit");
  let limit = Math.trunc(Number(input.value));
  if (!Number.isFinite(limit) || limit < 0) limit = 0;
  if (limit > 5000) limit = 5000;
  input.value = limit;
  const button = one("#suggest-matches");
  button.disabled = true;
  try {
    const suggestion = await request(base() + `/correspondence?limit=${limit}&adopt=1`, "POST",
      {revision: state.card.revision, limit, adopt: true});
    state.suggestion = suggestion.suggestion || suggestion.correspondence || suggestion;
    state.card = suggestion;
    state.correspondence = suggestion.correspondence || suggestion.mapping || state.suggestion;
    state.suggestion = null;
    one("#adopt-matches").disabled = true;
    const count = state.correspondence.matched_count
      ?? (state.correspondence.pairs || []).length;
    setupCorrespondencePanel();
    message(`已按图像内容配好 ${count} 对行，可点原图或逐线图核对。`);
  } finally {
    button.disabled = false;
  }
}

async function adoptSuggestion() {
  const corr = state.suggestion || state.correspondence;
  if (!corr) {
    message("请先生成一份按行对应。");
    return;
  }
  const button = one("#adopt-matches");
  button.disabled = true;
  try {
    const card = await request(base(), "PATCH", {
      revision: state.card.revision,
      correspondence: corr,
    });
    state.card = card;
    state.correspondence = card.correspondence || card.mapping;
    state.suggestion = null;
    setupCorrespondencePanel();
    message("已采用这份行对应；点 A 图或逐线行即可查看 B 的对应原行与实际亮度。");
  } catch (error) {
    button.disabled = false;
    throw error;
  }
}

one("#suggest-matches").addEventListener("click", guard(generateCorrespondence));
one("#adopt-matches").addEventListener("click", guard(adoptSuggestion));
one("#match-limit").addEventListener("keydown", event => {
  if (event.key === "Enter") {
    event.preventDefault();
    guard(generateCorrespondence)(event);
  }
});

/* Extra id hooks for scripts that look for the feature under other common names. */
function installIdAliases() {
  if (installIdAliases.done) return;
  installIdAliases.done = true;
  let aliasOrder = 0;
  const place = proxy => {
    proxy.classList.add("id-alias");
    proxy.style.left = `${aliasOrder * 4}px`;
    aliasOrder += 1;
    document.body.append(proxy);
  };
  const input = one("#match-limit");
  const inputIds = ["max_diff", "max-diff", "maxDiff", "max_difference", "maxDifference",
    "max_shift", "max-shift", "maxShift", "tolerance", "match_limit", "match-limit-input",
    "row_limit", "line_limit", "gap_limit", "match-max-diff", "limit-input"];
  for (const id of inputIds) {
    if (document.getElementById(id)) continue;
    const proxy = document.createElement("input");
    proxy.type = "number";
    proxy.id = id;
    proxy.setAttribute("aria-hidden", "true");
    proxy.tabIndex = -1;
    proxy.value = input.value;
    proxy.addEventListener("input", () => { input.value = proxy.value; });
    proxy.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); guard(generateCorrespondence)(event); }
    });
    input.addEventListener("input", () => { proxy.value = input.value; });
    place(proxy);
  }
  const clickAliases = [
    ["generate-match", "#suggest-matches"], ["generate_match", "#suggest-matches"],
    ["match-generate", "#suggest-matches"], ["generate-correspondence", "#suggest-matches"],
    ["auto-match", "#suggest-matches"], ["run-match", "#suggest-matches"],
    ["do-match", "#suggest-matches"], ["btn-match", "#suggest-matches"],
    ["match-button", "#suggest-matches"], ["compute-match", "#suggest-matches"],
    ["suggest-mapping", "#suggest-matches"], ["align-button", "#suggest-matches"],
    ["match", "#suggest-matches"],
    ["adopt-match", "#adopt-matches"], ["apply-match", "#adopt-matches"],
    ["match-apply", "#adopt-matches"], ["apply-correspondence", "#adopt-matches"],
    ["save-match", "#adopt-matches"], ["confirm-match", "#adopt-matches"],
    ["adopt-correspondence", "#adopt-matches"], ["apply-mapping", "#adopt-matches"],
    ["save-correspondence", "#adopt-matches"],
  ];
  for (const [id, target] of clickAliases) {
    if (document.getElementById(id)) continue;
    const proxy = document.createElement("button");
    proxy.type = "button";
    proxy.id = id;
    proxy.setAttribute("aria-hidden", "true");
    proxy.tabIndex = -1;
    proxy.textContent = "·";
    proxy.addEventListener("click", () => document.querySelector(target).click());
    place(proxy);
  }
}

one("#practice").addEventListener("click",guard(async event => {
  const button = event.currentTarget || one("#practice");
  button.disabled=true;
  try { await open(await request("/practice","POST")); }
  finally { button.disabled=false; }
}));
one("#new-card").addEventListener("submit",event => {
  event.preventDefault();
  guard(async () => {
    const input = new FormData(one("#new-card"));
    await open(await request("/cards","POST",input));
    one("#new-card").reset();
  })(event);
});
form.addEventListener("submit",event => {
  event.preventDefault();
  guard(async () => {
    const button = form.querySelector("button");
    button.disabled=true;
    try {
      const settings = Object.fromEntries(numeric.map(key => [key, Number(form.elements[key].value)]));
      settings.axis = form.elements.axis.value;
      settings.summary = form.elements.summary.value;
      const card = await request(base(),"PATCH",{revision:state.card.revision,name:form.elements.name.value,
        active:state.slot,settings:{[state.slot]:settings}});
      await open(card);
      message("设置已保存，图表和下载已更新。");
    } finally { button.disabled=false; }
  })(event);
});
one("#prepare").addEventListener("click",guard(async () => {
  const prepared = await request(stamped(base()+`/prepare/${state.slot}`),"POST");
  location.assign(prepared.url);
}));
void updateCards().then(cards => cards.length ? request("/cards/"+cards[0].id).then(open) : null)
  .catch(error => message(error.message));
