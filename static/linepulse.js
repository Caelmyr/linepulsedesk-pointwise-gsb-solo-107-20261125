const one = selector => document.querySelector(selector);
const form = one("#settings");
const canvas = one("#capture");
const chart = one("#profile");
const state = {card:null, slot:null, report:null, image:null, selected:null, drag:null, draft:null, load:0, commonResults:null};
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
  state.commonResults = null;
  one("#empty").hidden = true;
  one("#workspace").hidden = false;
  await show(card.active);
  await updateCards();
}

async function show(slot) {
  state.slot = slot;
  state.selected = null;
  state.report = null;
  state.image = null;
  state.drag = null;
  state.draft = null;
  const load = ++state.load;
  const photo = entry();
  one("#card-title").textContent = state.card.name;
  one("#annotation").textContent = `${state.card.synthetic ? "合成练习 · " : ""}r${state.card.revision} · ${photo.name}`;
  one("#photo-size").textContent = `${photo.width} × ${photo.height} px`;
  one("#point-reading").textContent = "点原图或剖面查看实际读出线。拖动原图可重画选区。";
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
  renderCommon();
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
  for (const {data,color,dash} of series) {
    context.strokeStyle = color;
    context.lineWidth = 1.3;
    context.setLineDash(dash || []);
    context.beginPath();
    let started = false;
    data.forEach((value,i) => {
      if (value == null || !Number.isFinite(value)) {
        started = false;
        return;
      }
      const x = transformX(i), y = transformY(value);
      if (!started) { context.moveTo(x,y); started = true; }
      else context.lineTo(x,y);
    });
    context.stroke();
    context.setLineDash([]);
  }
}

function drawGraphs() {
  if (!state.report) return;
  const rows = state.report.lines;
  const measured = rows.map(item => item.measured), fitted = rows.map(item => item.fit);
  const commonFitted = rows.map(item => item.common_fit ?? null);
  const all = [...measured,...fitted,...commonFitted.filter(value => value != null)];
  const start = rows[0].index, end = rows.at(-1).index;
  const index = state.selected >= start && state.selected <= end && state.selected != null ? state.selected-start : null;
  const series = [
    {data:measured,color:"#7fe3cc"},
    {data:fitted,color:"#ffbf7d"},
  ];
  if (commonFitted.some(value => value != null)) {
    series.push({data:commonFitted,color:"#c79bff",dash:commonIsStale() ? [5,4] : []});
  }
  plots(chart,series,[Math.min(...all)-1,Math.max(...all)+1],
    [String(start),`${end} ${entry().settings.axis==="rows"?"行":"列"}`],index);
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
  one("#fit-report").textContent = `手动试放 ${fmt(fit.trial_hz,2)} Hz · 表观 ${fmt(fit.apparent_hz,2)} Hz · 相位 ${fmt(fit.phase_deg)}° · 暗端 ${fmt(100*summary.dark_fraction,2)}% · 亮端 ${fmt(100*summary.white_fraction,2)}%`
    + (state.report.common_fit ? ` ｜ 共同试放 ${fmt(state.report.common_fit.trial_hz,2)} Hz · 偏差 ${fmt(state.report.common_fit.error,2)} 级${commonIsStale() ? " · 设置已改，待重新核对" : ""}` : "");
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
  const stamp = state.load;
  drawCapture(); drawGraphs();
  const report = await request(stamped(base()+`/line/${state.slot}?index=${index}`));
  if (stamp !== state.load || index !== state.selected) return;
  const axis = report.axis === "rows" ? "行" : "列";
  const row = report.line;
  one("#point-reading").textContent = row
    ? `原图第 ${axis} ${row.index} · t=${fmt(row.time_us,1)} µs · 实测 ${fmt(row.measured,2)} · 手动试放 ${fmt(row.fit,2)}`
      + (row.common_fit != null ? ` · 共同试放 ${fmt(row.common_fit,2)}` : "")
    : `原图第 ${axis} ${index} 在选区外。`;
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

const criticalKeys = ["x", "y", "w", "h", "axis", "line_us"];

function commonTrial() {
  return state.card?.common_trial || null;
}

function commonChangedSlots(trial = commonTrial()) {
  if (!trial?.critical || !state.card) return [];
  return ["a", "b"].filter(slot => {
    const saved = trial.critical[slot];
    const current = state.card.photos[slot]?.settings;
    if (!saved || !current) return false;
    return criticalKeys.some(key => String(saved[key]) !== String(current[key]));
  });
}

function commonIsStale() {
  return commonChangedSlots().length > 0;
}

function commonCriteria() {
  return Object.fromEntries(["min_hz", "max_hz", "min_amplitude", "tolerance_hz"]
    .map(key => [key, Number(one("#common-form").elements[key].value)]));
}

function peakDescription(slot, match) {
  const name = slot === "a" ? "照片 A" : "照片 B";
  return `${name} 匹配实际峰 ${fmt(match.peak_hz, 2)} Hz · 幅度 ${fmt(match.amplitude, 1)} 级 · 频率差 ${fmt(match.difference_hz, 2)} Hz`;
}

function renderCommonAdoption() {
  const area = one("#common-adopted");
  const trial = commonTrial();
  if (!trial) {
    area.replaceChildren();
    return;
  }
  const changed = commonChangedSlots(trial);
  const match = trial.match || {};
  const banner = document.createElement("div");
  banner.className = "adopted-banner" + (changed.length ? " stale" : "");
  banner.dataset.testid = changed.length ? "common-stale" : "common-adopted-banner";
  const detail = document.createElement("div");
  detail.innerHTML = `<strong>已采用共同试放 ${fmt(trial.frequency_hz, 2)} Hz</strong>` +
    (match.a && match.b ? `<p>${escape(peakDescription("a", match.a))}<br>${escape(peakDescription("b", match.b))}</p>` : "") +
    (changed.length ? `<p class="stale-warning">${changed.map(s => s.toUpperCase()).join("、")} 的选区、读出方向或每线用时已改动；旧共同试放需重新核对。手动试放仍可继续使用。</p>`
      : `<p class="ok">两张逐线图和 CSV 已加入紫色共同试放列/曲线。</p>`);
  const recheck = document.createElement("button");
  recheck.type = "button";
  recheck.className = "quiet";
  recheck.id = "recheck-common";
  recheck.textContent = changed.length ? "重新核对共同频率" : "按原条件重新搜索";
  recheck.addEventListener("click", guard(async () => {
    const criteria = trial.criteria || commonCriteria();
    for (const [key, value] of Object.entries(criteria)) one("#common-form").elements[key].value = value;
    await searchCommon(recheck);
    one("#common-results").scrollIntoView({behavior: "smooth", block: "nearest"});
  }));
  banner.append(detail, recheck);
  area.replaceChildren(banner);
}

function adoptButton(frequency, text, className="quiet") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.dataset.adoptFrequency = frequency;
  button.dataset.testid = "common-adopt";
  button.textContent = text;
  const synonyms = document.createElement("span");
  synonyms.className = "visually-hidden";
  synonyms.textContent = " 采用共同频率 应用共同试放";
  button.append(synonyms);
  button.addEventListener("click", guard(() => adoptCommon(frequency, button)));
  return button;
}

function renderCommonResults() {
  const area = one("#common-results");
  area.replaceChildren();
  const result = state.commonResults;
  if (!result) {
    const hint = document.createElement("p");
    hint.className = "caption";
    hint.textContent = "搜索会把每个真实频率分别按 A、B 的每线用时折叠到表观频率；只有两张都找到超门槛实际峰时才列为共同频率。";
    area.append(hint);
    return;
  }
  if (!result.candidates.length) {
    const empty = document.createElement("p");
    empty.className = "no-match";
    empty.dataset.testid = "common-no-match";
    empty.textContent = "没有两张同时匹配的共同频率；无匹配或只有单张能对上的频率均不采用。";
    area.append(empty);
    return;
  }
  const recommended = result.recommended;
  if (recommended) {
    const top = document.createElement("div");
    top.className = "recommend-row";
    top.dataset.testid = "common-recommendation";
    const label = document.createElement("strong");
    label.textContent = `建议：${fmt(recommended.frequency_hz, 2)} Hz`;
    const reason = document.createElement("span");
    reason.className = "caption";
    reason.textContent = `综合两张峰强度与较弱峰明显程度评分 ${fmt(recommended.score, 1)}；较弱峰 ${fmt(recommended.minimum_amplitude, 1)} 级，${recommended.weak_peak_clear ? "仍明显" : "明显程度偏弱"}（相对强度 ${fmt(100 * recommended.weakest_relative_strength, 0)}%，高过噪声底 ${fmt(recommended.weakest_prominence, 1)} 倍）。`;
    const adopt = adoptButton(recommended.frequency_hz, `采用建议 ${fmt(recommended.frequency_hz, 2)} Hz`, "primary");
    adopt.id = "adopt-common";
    top.append(label, reason, adopt);
    area.append(top);
  }
  for (const candidate of result.candidates) {
    const card = document.createElement("div");
    card.className = "candidate" + (candidate.recommended ? " recommended" : "");
    card.dataset.testid = "common-candidate";
    const heading = document.createElement("div");
    heading.className = "candidate-heading";
    const title = document.createElement("strong");
    title.textContent = `${fmt(candidate.frequency_hz, 2)} Hz`;
    heading.append(title);
    if (candidate.recommended) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "建议";
      heading.append(badge);
    }
    const score = document.createElement("span");
    score.className = "caption";
    score.textContent = `评分 ${fmt(candidate.score, 1)} · 较弱峰 ${fmt(candidate.minimum_amplitude, 1)} 级`;
    heading.append(score, adoptButton(candidate.frequency_hz, "采用此频率"));
    const peaks = document.createElement("div");
    peaks.className = "match-peaks";
    for (const slot of ["a", "b"]) {
      const line = document.createElement("span");
      line.className = "chip";
      line.textContent = peakDescription(slot, candidate.matches[slot]);
      peaks.append(line);
    }
    card.append(heading, peaks);
    area.append(card);
  }
}

function renderCommon() {
  if (!state.card) return;
  const panel = document.querySelector("#common-frequency, #common-panel");
  if (panel) panel.hidden = Object.keys(state.card.photos).length < 2;
  const criteria = commonTrial()?.criteria;
  if (criteria) {
    for (const key of ["min_hz", "max_hz", "min_amplitude", "tolerance_hz"]) {
      one("#common-form").elements[key].value = criteria[key];
    }
  }
  renderCommonAdoption();
  renderCommonResults();
}

async function searchCommon(button) {
  const form = one("#common-form");
  if (!form.reportValidity()) return;
  const criteria = commonCriteria();
  button = button || form.querySelector("button");
  button.disabled = true;
  try {
    state.commonResults = await request(base() + "/common-frequency", "POST",
      {revision: state.card.revision, ...criteria});
    renderCommonResults();
    one("#common-results").scrollIntoView({behavior: "smooth", block: "nearest"});
  } finally {
    button.disabled = false;
  }
}

async function adoptCommon(frequency, button) {
  const criteria = commonCriteria();
  if (button) button.disabled = true;
  try {
    const card = await request(base() + "/common-trial", "POST", {
      revision: state.card.revision, ...criteria, frequency_hz: Number(frequency),
    });
    state.card = card;
    await show(state.slot);
    message("已采用共同试放；两张原图线号不变，CSV 增加 common_fit 列。");
  } finally {
    if (button) button.disabled = false;
  }
}

one("#common-form").addEventListener("submit", event => {
  event.preventDefault();
  guard(() => searchCommon())(event);
});

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
