"use strict";

const $ = (id) => document.getElementById(id);

let options = null;
let upload = null;
let logoId = "";
let blurBoxes = [];
let activeJob = null;
let logCursor = 0;
let pollTimer = null;
let narration = { jobId: null, items: [], index: 1 };
let logStick = true;

function showError(err) {
  const message = err && err.message ? err.message : String(err || "Something went wrong");
  $("jobMessage").textContent = message;
  $("dockStatus").textContent = message;
}

async function api(path, opts = {}) {
  const headers = { Accept: "application/json" };
  const body = opts.body;
  if (body !== undefined && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { credentials: "same-origin", ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (res.status === 401) {
    location.href = "/";
    throw new Error("Login required");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : (res.statusText || "Request failed"));
  }
  return data;
}

function fillSelect(select, items, getValue, getLabel) {
  const current = select.value;
  select.innerHTML = "";
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = getValue(item);
    option.textContent = getLabel(item);
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function applySettings(settings) {
  const set = (id, value) => { if ($(id) && value !== undefined && value !== null) $(id).value = value; };
  set("source_language", settings.source_language);
  set("voice", settings.voice);
  set("gemini_direction", settings.gemini_direction || "");
  set("edge_rate", settings.edge_rate_percent ?? 0);
  set("video_quality", settings.video_quality || "720");
  set("output_ratio", settings.output_ratio || "16:9");
  set("frame_fill", settings.frame_fill || "blur");
  set("zoom_factor", settings.zoom_factor ?? 1.05);
  set("voice_volume", settings.voice_volume_percent ?? 100);
  set("whisper_model_other", settings.whisper_model_other || "small");
  set("title_text", settings.title_text || "");
  set("title_font_size", settings.title_font_size ?? 54);
  set("title_x", (settings.title_position || [0.5, 0.12])[0]);
  set("title_y", (settings.title_position || [0.5, 0.12])[1]);
  set("extra_text", settings.extra_text || "");
  set("extra_text_font_size", settings.extra_text_font_size ?? 42);
  set("extra_x", (settings.extra_text_position || [0.5, 0.84])[0]);
  set("extra_y", (settings.extra_text_position || [0.5, 0.84])[1]);
  $("short_pauses").checked = settings.short_pauses !== false;
  $("mirror_video").checked = settings.mirror_video !== false;
  $("reuse").checked = settings.reuse !== false;
  const sub = settings.subtitles || {};
  $("sub_enabled").checked = sub.enabled !== false;
  if (sub.font) $("sub_font").value = sub.font;
  set("sub_size", sub.size ?? 48);
  set("sub_color", sub.color || "#FFFFFF");
  $("sub_bold").checked = !!sub.bold;
  $("sub_box").checked = sub.box_enabled !== false;
  set("sub_x", (sub.position || [0.5, 0.92])[0]);
  set("sub_y", (sub.position || [0.5, 0.92])[1]);
  logoId = settings.logo_id || "";
  $("logo_enabled").checked = !!settings.logo_enabled && !!logoId;
  set("logo_width", settings.logo_width_fraction ?? 0.16);
  set("logo_opacity", settings.logo_opacity ?? 0.65);
  set("logo_x", (settings.logo_position || [0.8, 0.06])[0]);
  set("logo_y", (settings.logo_position || [0.8, 0.06])[1]);
  blurBoxes = Array.isArray(settings.blur_boxes) ? settings.blur_boxes.map((box) => ({ ...box })) : [];
  $("edge_rate_label").textContent = formatRate($("edge_rate").value);
  toggleGemini();
  renderBlurs();
  placeMarks();
}

function formatRate(value) {
  const number = Number(value) || 0;
  return (number >= 0 ? "+" : "") + number + "%";
}

function num(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function recapSettings() {
  return {
    source_language: $("source_language").value,
    voice: $("voice").value,
    gemini_direction: $("gemini_direction").value,
    edge_rate_percent: Number($("edge_rate").value),
    short_pauses: $("short_pauses").checked,
    mirror_video: $("mirror_video").checked,
    reuse: $("reuse").checked,
    voice_volume_percent: num("voice_volume", 100),
    zoom_factor: num("zoom_factor", 1.05),
    output_ratio: $("output_ratio").value,
    frame_fill: $("frame_fill").value,
    video_quality: $("video_quality").value,
    whisper_model_other: $("whisper_model_other").value,
    title_text: $("title_text").value,
    title_font_size: num("title_font_size", 54),
    title_position: [num("title_x", 0.5), num("title_y", 0.12)],
    extra_text: $("extra_text").value,
    extra_text_font_size: num("extra_text_font_size", 42),
    extra_text_position: [num("extra_x", 0.5), num("extra_y", 0.84)],
    logo_id: logoId,
    logo_enabled: $("logo_enabled").checked && !!logoId,
    logo_width_fraction: num("logo_width", 0.16),
    logo_opacity: num("logo_opacity", 0.65),
    logo_position: [num("logo_x", 0.8), num("logo_y", 0.06)],
    blur_boxes: blurBoxes,
    subtitles: {
      enabled: $("sub_enabled").checked,
      font: $("sub_font").value || "Noto Sans Myanmar",
      size: num("sub_size", 48),
      bold: $("sub_bold").checked,
      color: $("sub_color").value || "#FFFFFF",
      box_enabled: $("sub_box").checked,
      position: [num("sub_x", 0.5), num("sub_y", 0.92)],
    },
  };
}

function splitSettings() {
  return {
    minutes: num("minutes", 3),
    smart_cut: $("smart_cut").checked,
    max_extra: num("max_extra", 20),
    ratio: $("split_ratio").value,
    fill: $("split_fill").value,
    quality: $("split_quality").value,
    text: $("split_text").value,
    text_size: num("split_text_size", 42),
    label_on: $("label_on").checked,
    label_template: $("label_template").value || "Part {n}/{total}",
  };
}

function toggleGemini() {
  $("geminiWrap").classList.toggle("hidden", !$("voice").value.startsWith("gemini:"));
}

function clock(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m + ":" + String(s).padStart(2, "0");
}

function setUpload(item) {
  upload = item;
  if (!item) {
    $("uploadName").textContent = "ဗီဒီယို မရွေးရသေးပါ။";
    $("stage").classList.add("hidden");
    return;
  }
  const length = item.duration ? " · " + clock(item.duration) : "";
  $("uploadName").textContent = item.name + length + " · " + Math.round((item.size || 0) / 1024 / 1024) + " MB";
  const slider = $("frameSlider");
  const duration = item.duration || 1;
  slider.max = String(Math.max(0.1, duration));
  slider.value = String(Math.min(1, duration / 10));
  loadFrame();
  if ($("savedUploads").value !== item.id) {
    const exists = [...$("savedUploads").options].some((option) => option.value === item.id);
    if (!exists) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      $("savedUploads").appendChild(option);
    }
    $("savedUploads").value = item.id;
  }
}

function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.withCredentials = true;
    const bar = $("uploadProgress");
    bar.classList.remove("hidden");
    bar.value = 0;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) bar.value = event.loaded / event.total;
    };
    xhr.onload = () => {
      bar.classList.add("hidden");
      let data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (_) { data = {}; }
      if (xhr.status === 401) { location.href = "/"; return; }
      if (xhr.status >= 400) reject(new Error(data.detail || "Upload failed"));
      else resolve(data);
    };
    xhr.onerror = () => { bar.classList.add("hidden"); reject(new Error("Upload failed. Check the connection.")); };
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}

async function onFile(file) {
  if (!file) return;
  try {
    $("dockStatus").textContent = "Uploading " + file.name + " …";
    const saved = await uploadFile(file);
    setUpload(saved);
    $("dockStatus").textContent = "Uploaded. Press Start when the settings look right.";
  } catch (err) {
    showError(err);
  }
}

async function loadFrame() {
  if (!upload) return;
  const t = Number($("frameSlider").value) || 0;
  $("frameClock").textContent = clock(t);
  $("frameImg").src = "/api/uploads/" + encodeURIComponent(upload.id) + "/frame.jpg?t=" + encodeURIComponent(t);
  $("stage").classList.remove("hidden");
}

function placeMarks() {
  const put = (id, x, y) => {
    const el = $(id);
    el.style.left = (Number(x) * 100) + "%";
    el.style.top = (Number(y) * 100) + "%";
  };
  put("markTitle", $("title_x").value, $("title_y").value);
  put("markExtra", $("extra_x").value, $("extra_y").value);
  put("markSub", $("sub_x").value, $("sub_y").value);
  put("markLogo", $("logo_x").value, $("logo_y").value);
  document.querySelectorAll(".blur").forEach((el) => el.remove());
  const stage = $("stage");
  blurBoxes.forEach((box, index) => {
    const el = document.createElement("div");
    el.className = "blur";
    el.style.left = (box.x * 100) + "%";
    el.style.top = (box.y * 100) + "%";
    el.style.width = (box.w * 100) + "%";
    el.style.height = (box.h * 100) + "%";
    el.title = "Blur " + (index + 1);
    stage.appendChild(el);
    drag(el, (x, y) => {
      box.x = Math.min(0.98, Math.max(0, x - box.w / 2));
      box.y = Math.min(0.98, Math.max(0, y - box.h / 2));
      el.style.left = (box.x * 100) + "%";
      el.style.top = (box.y * 100) + "%";
      renderBlurs();
    });
  });
}

function renderBlurs() {
  const list = $("blurList");
  list.innerHTML = "";
  blurBoxes.forEach((box, index) => {
    const row = document.createElement("p");
    row.className = "muted";
    row.textContent = "Blur " + (index + 1) + " · x " + box.x.toFixed(2) + " y " + box.y.toFixed(2)
      + " w " + box.w.toFixed(2) + " h " + box.h.toFixed(2);
    list.appendChild(row);
  });
}

function drag(el, onMove) {
  el.addEventListener("pointerdown", (event) => {
    const img = $("frameImg");
    const rect = img.getBoundingClientRect();
    const move = (e) => {
      const x = (e.clientX - rect.left) / rect.width;
      const y = (e.clientY - rect.top) / rect.height;
      onMove(Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    event.preventDefault();
  });
}

function bindMark(id, xId, yId) {
  drag($(id), (x, y) => {
    $(xId).value = x.toFixed(2);
    $(yId).value = y.toFixed(2);
    placeMarks();
  });
}

function renderJob(job) {
  activeJob = job;
  const pct = Math.round((Number(job.progress) || 0) * 100);
  $("jobProgress").style.width = pct + "%";
  const ahead = job.queue_ahead ? " · queue " + job.queue_ahead : "";
  $("jobLabel").textContent = (job.stage || job.status || "") + (job.progress_label ? " · " + job.progress_label : "") + ahead;
  $("jobMessage").textContent = job.message || job.source_name || "";
  $("dockStatus").textContent = (job.source_name || "Job") + " · " + job.status + " · " + pct + "%";
  $("stopJob").disabled = !(job.status === "running" || job.status === "queued");
  const box = $("downloads");
  box.innerHTML = "";
  (job.downloads || []).forEach((item) => {
    if (item.kind === "log") return;
    const link = document.createElement("a");
    const href = item.kind === "part"
      ? "/api/jobs/" + job.id + "/download/part/" + encodeURIComponent(item.name)
      : "/api/jobs/" + job.id + "/download/" + encodeURIComponent(item.kind);
    link.href = href;
    link.textContent = "ဒေါင်းလုဒ် · " + item.name + " (" + Math.max(1, Math.round(item.size / 1024)) + " KB)";
    link.setAttribute("download", item.name);
    box.appendChild(link);
  });
  if (job.plan_ready) {
    $("narrationPanel").classList.remove("hidden");
    if (narration.jobId !== job.id) loadNarration(job.id);
  }
}

function appendLog(lines) {
  if (!lines || !lines.length) return;
  const el = $("log");
  el.textContent += (el.textContent ? "\n" : "") + lines.join("\n");
  if (logStick) el.scrollTop = el.scrollHeight;
}

async function poll() {
  if (!activeJob) return;
  try {
    const job = await api("/api/jobs/" + activeJob.id);
    renderJob(job);
    const page = await api("/api/jobs/" + activeJob.id + "/log?after=" + logCursor);
    appendLog(page.lines);
    logCursor = page.next;
    if (job.status === "queued" || job.status === "running" || (page.total > page.next)) {
      pollTimer = setTimeout(poll, page.total > page.next ? 200 : 1500);
    } else {
      loadJobs();
    }
  } catch (err) {
    showError(err);
  }
}

function watch(job, fromStart) {
  if (pollTimer) clearTimeout(pollTimer);
  activeJob = job;
  logCursor = fromStart ? 0 : -1;
  $("log").textContent = "";
  logStick = true;
  renderJob(job);
  poll();
}

async function loadJobs() {
  const data = await api("/api/jobs");
  const list = $("jobList");
  list.innerHTML = "";
  if (!data.jobs.length) {
    list.innerHTML = "<p class='muted'>အလုပ် မရှိသေးပါ။</p>";
    return;
  }
  data.jobs.forEach((job) => {
    const row = document.createElement("div");
    row.className = "job";
    const text = document.createElement("div");
    text.innerHTML = "<strong>" + escapeHtml(job.source_name || job.id) + "</strong> "
      + "<span class='badge " + escapeHtml(job.status) + "'>" + escapeHtml(job.status) + "</span>"
      + "<br><span class='muted'>" + escapeHtml(job.kind) + " · " + escapeHtml(job.created || "") + "</span>";
    const actions = document.createElement("div");
    actions.className = "row";
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "ဖွင့်";
    open.addEventListener("click", () => watch(job, false));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "ဖျက်";
    remove.addEventListener("click", async () => {
      const also = confirm("ဖိုင်တွေပါ ဖျက်မလား? OK = delete files, Cancel = remove from the list only.");
      if (!confirm("ဒီအလုပ်ကို ဖျက်မလား?")) return;
      try {
        await api("/api/jobs/" + job.id + (also ? "?files=1" : ""), { method: "DELETE" });
        if (activeJob && activeJob.id === job.id) activeJob = null;
        loadJobs();
      } catch (err) { showError(err); }
    });
    actions.append(open, remove);
    row.append(text, actions);
    list.appendChild(row);
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[ch]));
}

async function loadNarration(jobId) {
  const data = await api("/api/jobs/" + jobId + "/narration");
  narration = { jobId, items: data.items || [], index: narration.jobId === jobId ? narration.index : 1 };
  if (!narration.items.length) return;
  if (narration.index < 1 || narration.index > narration.items.length) narration.index = 1;
  await showSegment(narration.index);
}

async function showSegment(index) {
  if (!narration.jobId) return;
  const row = await api("/api/jobs/" + narration.jobId + "/narration/" + index);
  narration.index = row.index;
  $("segIndex").value = row.index;
  $("segCount").textContent = row.index + " / " + narration.items.length;
  $("segSource").textContent = row.source_text || "";
  $("segText").value = row.narration || "";
  $("segInfo").textContent = (row.timestamp || "") + " · " + row.video_start + "–" + row.video_end + "s · "
    + (row.wav_ready ? "voice already made" : "voice not made yet");
  $("segProblems").textContent = row.problems && row.problems.length ? "⚠ " + row.problems.join(" · ") : "ပြဿနာ မတွေ့ပါ။";
  $("segProblems").className = row.problems && row.problems.length ? "error" : "ok";
}

async function saveSegment() {
  if (!narration.jobId) return;
  try {
    const row = await api("/api/jobs/" + narration.jobId + "/narration/" + narration.index, {
      method: "PUT",
      body: JSON.stringify({ text: $("segText").value }),
    });
    $("segProblems").textContent = row.problems && row.problems.length
      ? "သိမ်းပြီး။ ⚠ " + row.problems.join(" · ")
      : "သိမ်းပြီး။ Start / Resume နှိပ်ရင် ဒီစာကြောင်းကို အသံပြန်ထုတ်မယ်။";
    $("segProblems").className = row.problems && row.problems.length ? "warn" : "ok";
    const item = narration.items.find((entry) => entry.index === row.index);
    if (item) item.problems = !!(row.problems && row.problems.length);
  } catch (err) {
    showError(err);
  }
}

function requireUpload() {
  if (!upload) throw new Error("ဗီဒီယို အရင် တင်ပါ။ Upload a video first.");
  return upload.id;
}

async function start(kind) {
  try {
    const uploadId = requireUpload();
    const path = kind === "split" ? "/api/jobs/split" : "/api/jobs/recap";
    const settings = kind === "split" ? splitSettings() : recapSettings();
    const job = await api(path, { method: "POST", body: JSON.stringify({ upload_id: uploadId, settings }) });
    watch(job, true);
    loadJobs();
  } catch (err) {
    showError(err);
  }
}

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  $("tab-recap").classList.toggle("hidden", name !== "recap");
  $("tab-split").classList.toggle("hidden", name !== "split");
  $("tab-jobs").classList.toggle("hidden", name !== "jobs");
  $("startRecap").classList.toggle("hidden", name === "split");
  $("startSplit").classList.toggle("hidden", name !== "split");
  if (name === "jobs") loadJobs().catch(showError);
}

async function init() {
  options = await api("/api/options");
  const settings = options.settings || {};
  fillSelect($("source_language"), options.languages, (item) => item.id, (item) => item.label);
  fillSelect($("voice"), options.voices, (item) => item.id, (item) => item.label);
  fillSelect($("video_quality"), options.qualities, (item) => item.id, (item) => item.label);
  fillSelect($("split_quality"), options.qualities, (item) => item.id, (item) => item.label);
  fillSelect($("output_ratio"), options.ratios, (item) => item, (item) => item);
  fillSelect($("split_ratio"), options.ratios, (item) => item, (item) => item);
  fillSelect($("frame_fill"), options.fills, (item) => item.id, (item) => item.label);
  fillSelect($("split_fill"), options.fills, (item) => item.id, (item) => item.label);
  fillSelect($("whisper_model_other"), options.whisper_models, (item) => item, (item) => item);
  const fonts = options.fonts && options.fonts.length ? options.fonts : ["Noto Sans Myanmar"];
  fillSelect($("sub_font"), fonts, (item) => item, (item) => item);
  applySettings(settings);
  $("split_ratio").value = "Original";
  $("split_quality").value = settings.video_quality || "720";
  $("split_fill").value = "blur";
  const uploads = await api("/api/uploads");
  const select = $("savedUploads");
  select.innerHTML = "<option value=''>— ရွေးပါ —</option>";
  (uploads.uploads || []).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    select.appendChild(option);
  });
  if ((uploads.uploads || [])[0]) setUpload(uploads.uploads[0]);
  else setUpload(null);
  const jobs = await api("/api/jobs");
  const live = (jobs.jobs || []).find((job) => job.status === "running" || job.status === "queued");
  if (live) watch(live, false);
  loadJobs().catch(() => {});
  switchTab("recap");
}

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});
$("file").addEventListener("change", () => onFile($("file").files[0]));
$("drop").addEventListener("dragover", (event) => { event.preventDefault(); $("drop").classList.add("hot"); });
$("drop").addEventListener("dragleave", () => $("drop").classList.remove("hot"));
$("drop").addEventListener("drop", (event) => {
  event.preventDefault();
  $("drop").classList.remove("hot");
  onFile(event.dataTransfer.files[0]);
});
$("savedUploads").addEventListener("change", async () => {
  const id = $("savedUploads").value;
  if (!id) return;
  const data = await api("/api/uploads");
  const item = (data.uploads || []).find((row) => row.id === id);
  if (item) setUpload(item);
});
$("voice").addEventListener("change", toggleGemini);
$("edge_rate").addEventListener("input", () => { $("edge_rate_label").textContent = formatRate($("edge_rate").value); });
["title_x", "title_y", "extra_x", "extra_y", "sub_x", "sub_y", "logo_x", "logo_y"].forEach((id) => {
  $(id).addEventListener("input", placeMarks);
});
$("frameSlider").addEventListener("change", loadFrame);
$("addBlur").addEventListener("click", () => {
  blurBoxes.push({ x: 0.1, y: 0.08, w: 0.35, h: 0.12 });
  renderBlurs();
  placeMarks();
});
$("clearBlur").addEventListener("click", () => { blurBoxes = []; renderBlurs(); placeMarks(); });
bindMark("markTitle", "title_x", "title_y");
bindMark("markExtra", "extra_x", "extra_y");
bindMark("markSub", "sub_x", "sub_y");
bindMark("markLogo", "logo_x", "logo_y");
$("logo_file").addEventListener("change", async () => {
  const file = $("logo_file").files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const saved = await api("/api/logos", { method: "POST", body });
    logoId = saved.id;
    $("logo_enabled").checked = true;
    $("dockStatus").textContent = "Logo uploaded.";
  } catch (err) { showError(err); }
});
$("startRecap").addEventListener("click", () => start("recap"));
$("startSplit").addEventListener("click", () => start("split"));
$("stopJob").addEventListener("click", async () => {
  if (!activeJob) return;
  try { watch(await api("/api/jobs/" + activeJob.id + "/stop", { method: "POST" }), false); }
  catch (err) { showError(err); }
});
$("checkCuts").addEventListener("click", async () => {
  try {
    const id = requireUpload();
    $("cutList").textContent = "စစ်နေသည်…";
    const plan = await api("/api/uploads/" + id + "/split-plan", {
      method: "POST",
      body: JSON.stringify({ settings: splitSettings() }),
    });
    const rows = (plan.parts || []).map((part) =>
      "<tr><td>" + part.n + "</td><td>" + clock(part.start) + "</td><td>" + clock(part.end)
      + "</td><td>" + part.seconds + "s</td><td>" + escapeHtml(part.cut) + "</td></tr>").join("");
    $("cutList").innerHTML = "<table class='cuts'><tr><th>#</th><th>Start</th><th>End</th><th>Length</th><th>Cut</th></tr>"
      + rows + "</table>";
  } catch (err) {
    $("cutList").textContent = "";
    showError(err);
  }
});
$("segPrev").addEventListener("click", () => showSegment(Math.max(1, narration.index - 1)).catch(showError));
$("segNext").addEventListener("click", () => showSegment(narration.index + 1).catch(showError));
$("segGo").addEventListener("click", () => showSegment(Number($("segIndex").value) || 1).catch(showError));
$("segProblem").addEventListener("click", () => {
  const next = narration.items.find((item) => item.index > narration.index && item.problems)
    || narration.items.find((item) => item.problems);
  if (!next) { $("segProblems").textContent = "ပြဿနာရှိတဲ့ စာကြောင်း မတွေ့တော့ပါ။"; return; }
  showSegment(next.index).catch(showError);
});
$("segSave").addEventListener("click", saveSegment);
$("log").addEventListener("scroll", () => {
  const el = $("log");
  logStick = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
});

init().catch(showError);
