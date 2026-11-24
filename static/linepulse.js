const one = selector => document.querySelector(selector);
const form = one("#settings");
const canvas = one("#capture");
const chart = one("#profile");
const state = {card:null, slot:null, report:null, image:null, selected:null, drag:null, draft:null, load:0, proof:null, proofLoad:0};
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
  state.proof = null;
  state.proofLoad = 0;
  const load = ++state.load;
  const photo = entry();
  one("#card-title").textContent = state.card.name;
  one("#annotation").textContent = `${state.card.synthetic ? "合成练习 · " : ""}r${state.card.revision} · ${photo.name}`;
  one("#photo-size").textContent = `${photo.width} × ${photo.height} px`;
  one("#point-reading").textContent = "点原图或剖面查看实际读出线。拖动原图可重画选区。";
  const proofBudget = one("#proof-budget");
  proofBudget.value = 240;
  proofBudget.max = photo.settings.axis === "rows" ? photo.height : photo.width;
  one("#proof-report").classList.remove("error");
  one("#proof-report").textContent = "短校样与校样 PNG 使用同一套保留线号，点保留点可回查真实线号。";
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
  loadShortProof();
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
  drawShortProof();
}

function proofQuery() {
  const params = new URLSearchParams();
  const budgetInput = one("#proof-budget");
  const budget = Math.trunc(Number(budgetInput.value));
  if (Number.isFinite(budget)) params.set("budget", String(Math.max(2, budget)));
  if (state.selected != null) params.set("anchor", String(state.selected));
  return params;
}

async function loadShortProof() {
  if (!state.report) return;
  const load = ++state.proofLoad;
  const params = proofQuery();
  // Keep the PNG download on exactly the same parameters, so the link can
  // never silently serve an older retained set while the panel shows an error.
  one("#png").href = stamped(base() + `/export/${state.slot}/proof.png`) + "&" + params.toString();
  const url = stamped(base() + `/shortproof/${state.slot}`) + "&" + params.toString();
  let proof;
  try {
    proof = await request(url);
  } catch (error) {
    if (load === state.proofLoad) {
      state.proof = null;
      const note = one("#proof-report");
      note.classList.add("error");
      note.textContent = error.message;
      drawShortProof();
    }
    return;
  }
  if (load !== state.proofLoad) return;
  state.proof = proof;
  const note = one("#proof-report");
  note.classList.remove("error");
  const axis = entry().settings.axis === "rows" ? "行" : "列";
  const pinned = proof.pinned.map(index => `${axis}${index}`).join("、");
  const anchorText = proof.anchor == null ? "" : ` · 点选 ${axis}${proof.anchor}`;
  note.textContent = proof.complete
    ? `预算 ${proof.budget} 点不少于全部 ${proof.line_count} 条，已保留全部真实读出线（${pinned} 同条只算一次${anchorText}）。`
    : `保留 ${proof.retained.length}/${proof.line_count} 条真实${axis} · 必保 ${pinned}${anchorText} · 首尾与点选同条只算一次`;
  drawShortProof();
}

function drawShortProof() {
  const graph = one("#short-proof");
  const proof = state.proof;
  const context = graph.getContext("2d");
  const bounds = graph.getBoundingClientRect();
  const width = bounds.width, height = bounds.height;
  if (width < 80 || height < 45) return;
  const ratio = window.devicePixelRatio || 1;
  graph.width = Math.round(width * ratio);
  graph.height = Math.round(height * ratio);
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#a9bacb";
  context.strokeStyle = "#26384a";
  context.font = "10px monospace";
  const pad = {left:37, right:13, top:10, bottom:24};
  const right = width - pad.right;
  const bottom = height - pad.bottom;
  if (!proof || !proof.points.length) {
    context.fillStyle = "#8295a6";
    context.fillText("短校样暂不可用", pad.left, (height + 4) / 2);
    return;
  }
  const rows = state.report.lines;
  const start = rows[0].index, end = rows.at(-1).index;
  const span = Math.max(end - start, 1);
  const values = proof.points.flatMap(row => [row.measured, row.fit]);
  const lo = Math.min(...values) - 1, hi = Math.max(...values) + 1;
  const transformX = lineIndex => pad.left + (lineIndex - start) * (right - pad.left) / span;
  const transformY = value => bottom - (value - lo) * (bottom - pad.top) / Math.max(.01, hi - lo);
  for (let i = 0; i < 5; i++) {
    const value = lo + (hi - lo) * i / 4;
    const y = transformY(value);
    context.beginPath();
    context.moveTo(pad.left, y); context.lineTo(right, y); context.stroke();
    context.fillText(fmt(value,0),6,y+3);
  }
  const axis = entry().settings.axis === "rows" ? "行" : "列";
  context.fillText(`${axis}${start}`,pad.left,height-5);
  context.textAlign = "right"; context.fillText(`${axis}${end}`,right,height-5); context.textAlign = "left";
  if (state.selected != null && state.selected >= start && state.selected <= end) {
    const x = transformX(state.selected);
    context.strokeStyle = "#d2e2ee";
    context.setLineDash([4,3]);
    context.beginPath();
    context.moveTo(x,pad.top); context.lineTo(x,bottom); context.stroke();
    context.setLineDash([]);
  }
  const pinned = new Set(proof.pinned);
  for (const [key, color] of [["measured","#7fe3cc"],["fit","#ffbf7d"]]) {
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = 1.3;
    context.beginPath();
    proof.points.forEach((row, i) => {
      const x = transformX(row.index), y = transformY(row[key]);
      if (i === 0) context.moveTo(x,y);
      else context.lineTo(x,y);
    });
    context.stroke();
    for (const row of proof.points) {
      const x = transformX(row.index), y = transformY(row[key]);
      context.beginPath();
      context.arc(x, y, pinned.has(row.index) ? 3.2 : 2, 0, Math.PI * 2);
      context.fill();
    }
  }
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
  const stamp = state.load;
  drawCapture(); drawGraphs();
  loadShortProof();
  const report = await request(stamped(base()+`/line/${state.slot}?index=${index}`));
  if (stamp !== state.load || index !== state.selected) return;
  const axis = report.axis === "rows" ? "行" : "列";
  const row = report.line;
  one("#point-reading").textContent = row
    ? `原图第 ${axis} ${row.index} · t=${fmt(row.time_us,1)} µs · 实测 ${fmt(row.measured,2)} · 试放 ${fmt(row.fit,2)}`
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
one("#short-proof").addEventListener("click",guard(async event => {
  const proof = state.proof;
  if (!proof || !proof.points.length) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  const rows = state.report.lines;
  const start = rows[0].index, end = rows.at(-1).index;
  const padLeft = 37, padRight = 13;
  const fraction = Math.min(1,Math.max(0,(event.clientX-bounds.left-padLeft)/(bounds.width-padLeft-padRight)));
  const lineIndex = start + fraction * Math.max(end - start, 1);
  let nearest = proof.points[0].index;
  for (const row of proof.points) {
    if (Math.abs(row.index - lineIndex) < Math.abs(nearest - lineIndex)) nearest = row.index;
  }
  await selectLine(nearest);
}));
one("#proof-budget").addEventListener("change",guard(() => loadShortProof()));
window.addEventListener("resize",drawGraphs);

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
