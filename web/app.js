/* ============ xhs_clone Web UI 前端逻辑 ============ */
'use strict';

// ===== 全局状态 =====
const State = {
  paramGroups: [],       // 后端返回的分组定义
  presets: [],           // 预设方案
  voices: { edge: [], volcengine: [] },     // 配音列表
  filters: {},           // 滤镜按分类
  effects: {},           // 特效按分类
  aiCovers: { covers: [], rewrites: [], providers: [] },
  bgm: { available: [], recommendations: {}, styles: [] },
  values: {},            // 当前参数值（key -> value）
  activeGroup: null,     // 当前激活的分组 key
  running: false,
  taskStartTime: 0,
  progressTimer: null,
  sseSource: null,
  logSince: 0,
  history: [],           // 历史记录（localStorage）
  customPresets: [],     // 自定义模板（localStorage）
};

// localStorage 存储键
const STORAGE_KEYS = {
  THEME: "xhs_theme",
  HISTORY: "xhs_history",
  CUSTOM_PRESETS: "xhs_custom_presets",
  LLM_CONFIG: "xhs_llm_config",               // LLM 全量配置持久化（含 API Key，向后兼容）
  PROVIDER_CONFIGS: "xhs_llm_provider_configs", // 按服务商分别存的 key/token（切换服务商自动带出）
};

// 需要持久化的 LLM 参数 key（服务端 config/llm_config.json + 本地浏览器全量持久化）
const LLM_PERSIST_KEYS = [
  "llm", "llm_only", "llm_provider", "llm_model", "llm_api_key",
  "grsai_image_model",
  "prompt_llm_provider", "prompt_llm_model", "prompt_llm_api_key",
  "image_llm_provider", "image_llm_model", "image_llm_api_key",
  "image_llm_fallback_provider", "image_llm_fallback_model", "image_llm_fallback_api_key",
  "ai_cover", "ai_cover_styles", "ai_cover_size", "image_mode",
  "tts_provider",
  "volcengine_app_id", "volcengine_access_key", "elevenlabs_api_key", "use_s2s",
];

// 服务商字段映射：切换服务商时自动带出对应的 model / api_key / 额外字段
// scope: 存储分组；modelKey/apiKeyKey: 对应的 State.values 字段；extra: 额外随服务商存的字段
const PROVIDER_FIELDS = {
  llm_provider:        { scope: "main",   modelKey: "llm_model",        apiKeyKey: "llm_api_key" },
  prompt_llm_provider: { scope: "prompt", modelKey: "prompt_llm_model", apiKeyKey: "prompt_llm_api_key" },
  image_llm_provider:  { scope: "image",  modelKey: "image_llm_model",  apiKeyKey: "image_llm_api_key" },
  image_llm_fallback_provider: { scope: "image_fallback", modelKey: "image_llm_fallback_model", apiKeyKey: "image_llm_fallback_api_key" },
  tts_provider:        { scope: "tts",    modelKey: null,               apiKeyKey: null,
                         extra: { volcengine_app_id: "app_id", volcengine_access_key: "access_key", elevenlabs_api_key: "elevenlabs_key" } },
};

// 记录每个服务商字段上一次的值，用于检测切换
const _prevProvider = {};

// 阶段映射（与后端 _classify_line 对应）
const STAGES = [
  { key: "download", label: "下载", icon: "📥" },
  { key: "dedup", label: "去重", icon: "🔄" },
  { key: "enhance", label: "增强", icon: "✨" },
  { key: "subtitle", label: "字幕", icon: "💬" },
  { key: "voice", label: "配音", icon: "🎤" },
  { key: "bgm", label: "BGM", icon: "🎵" },
  { key: "copywrite", label: "文案", icon: "✍️" },
  { key: "cover", label: "封面", icon: "🖼️" },
  { key: "done", label: "完成", icon: "✅" },
];
const STAGE_INDEX = {};
STAGES.forEach((s, i) => STAGE_INDEX[s.key] = i);

// ===== DOM 引用 =====
const $ = (id) => document.getElementById(id);
const el = {
  presetSelect: $("presetSelect"),
  savePresetBtn: $("savePresetBtn"),
  deletePresetBtn: $("deletePresetBtn"),
  themeToggle: $("themeToggle"),
  helpBtn: $("helpBtn"),
  paramSearch: $("paramSearch"),
  clearSearch: $("clearSearch"),
  groupTabs: $("groupTabs"),
  groupPanels: $("groupPanels"),
  runBtn: $("runBtn"),
  stopBtn: $("stopBtn"),
  resetBtn: $("resetBtn"),
  runBarHint: $("runBarHint"),
  cmdPreview: $("cmdPreview"),
  cmdErrors: $("cmdErrors"),
  copyCmdBtn: $("copyCmdBtn"),
  refreshCmdBtn: $("refreshCmdBtn"),
  progressCard: $("progressCard"),
  progressStage: $("progressStage"),
  progressElapsed: $("progressElapsed"),
  progressFill: $("progressFill"),
  progressSteps: $("progressSteps"),
  logBox: $("logBox"),
  autoscrollChk: $("autoscrollChk"),
  clearLogBtn: $("clearLogBtn"),
  outputsCard: $("outputsCard"),
  outputsList: $("outputsList"),
  refreshOutputsBtn: $("refreshOutputsBtn"),
  toast: $("toast"),
  // 弹窗
  voiceModal: $("voiceModal"),
  auditionVoice: $("auditionVoice"),
  auditionRate: $("auditionRate"),
  auditionPitch: $("auditionPitch"),
  auditionText: $("auditionText"),
  auditionPlayBtn: $("auditionPlayBtn"),
  auditionHint: $("auditionHint"),
  auditionAudio: $("auditionAudio"),
  pickerModal: $("pickerModal"),
  pickerTitle: $("pickerTitle"),
  pickerBody: $("pickerBody"),
  helpModal: $("helpModal"),
};

// ===== 工具函数 =====
function toast(msg, type = "") {
  el.toast.textContent = msg;
  el.toast.className = "toast " + type;
  el.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.toast.hidden = true; }, 2600);
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function formatTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const ss = (s % 60).toString().padStart(2, "0");
  const mm = m.toString().padStart(2, "0");
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}:${mm}:${ss}`;
  }
  return `${mm}:${ss}`;
}

function fileIcon(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["mp4", "mov", "avi", "mkv", "webm", "flv"].includes(ext)) return "🎬";
  if (["mp3", "wav", "m4a", "aac", "flac", "ogg"].includes(ext)) return "🎵";
  if (["jpg", "jpeg", "png", "webp", "gif", "bmp"].includes(ext)) return "🖼️";
  if (["srt", "ass", "vtt"].includes(ext)) return "💬";
  if (["txt", "md"].includes(ext)) return "📄";
  if (["json"].includes(ext)) return "📋";
  return "📦";
}

// ===== 主题切换 =====
function initTheme() {
  const saved = localStorage.getItem("xhs_theme");
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("xhs_theme", next);
}

// ===== API 调用 =====
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText}`);
  return r.json();
}
async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

// ===== 初始化 =====
async function init() {
  initTheme();
  bindGlobalEvents();
  await loadParams();
  await Promise.all([
    loadVoices(),
    loadFilters(),
    loadEffects(),
    loadAiCovers(),
    loadBgm(),
  ]);
  // 用动态数据填充 choices
  applyDynamicChoices();
  renderGroups();
  loadCustomPresets();
  renderPresetOptions();
  loadHistory();
  // 恢复 LLM 配置（覆盖默认值，不覆盖 note/enhance_only）
  loadLlmConfig();
  // 初始化服务商切换追踪（必须在 loadLlmConfig 之后，确保用恢复后的值）
  initPrevProviders();
  // 默认载入 default 预设的初始值（但保留已恢复的 LLM 配置）
  applyPresetKeepLlm(State.presets[0], false);
  refreshCmdPreview();
  // 检查上次任务状态
  checkStatus();
}

function bindGlobalEvents() {
  el.themeToggle.addEventListener("click", toggleTheme);
  el.helpBtn.addEventListener("click", () => openModal(el.helpModal));
  el.presetSelect.addEventListener("change", onPresetChange);
  el.savePresetBtn.addEventListener("click", onSaveCustomPreset);
  el.deletePresetBtn.addEventListener("click", onDeleteCustomPreset);
  el.runBtn.addEventListener("click", onRun);
  el.stopBtn.addEventListener("click", onStop);
  el.resetBtn.addEventListener("click", onReset);
  el.copyCmdBtn.addEventListener("click", copyCmd);
  el.refreshCmdBtn.addEventListener("click", refreshCmdPreview);
  el.clearLogBtn.addEventListener("click", clearLog);
  el.refreshOutputsBtn.addEventListener("click", refreshOutputs);
  el.autoscrollChk.addEventListener("change", () => {});
  // 搜索
  el.paramSearch.addEventListener("input", onSearch);
  el.clearSearch.addEventListener("click", () => {
    el.paramSearch.value = "";
    onSearch();
    el.paramSearch.focus();
  });
  // 弹窗关闭
  document.querySelectorAll("[data-close]").forEach(node => {
    node.addEventListener("click", (e) => {
      const modal = e.currentTarget.closest(".modal");
      if (modal) modal.hidden = true;
    });
  });
  // 试听
  el.auditionPlayBtn.addEventListener("click", onAuditionPlay);
  // 键盘快捷键
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") {
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (e.key === "/" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      el.paramSearch.focus();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      onRun();
    } else if ((e.ctrlKey || e.metaKey) && e.key === ".") {
      e.preventDefault();
      onStop();
    }
  });
}

// ===== 加载参数定义 =====
async function loadParams() {
  try {
    const data = await apiGet("/api/params");
    State.paramGroups = data.groups || [];
    State.presets = data.presets || [];
    State.backendLlmConfig = data.llm_config || null;
    // 自定义模板优先用后端返回的，后端没有则回退到 localStorage（兼容旧数据）
    const backendCustom = data.custom_presets || [];
    if (backendCustom.length > 0) {
      State.customPresets = backendCustom;
      // 同步到 localStorage 做离线缓存
      try { localStorage.setItem(STORAGE_KEYS.CUSTOM_PRESETS, JSON.stringify(backendCustom)); } catch (_) {}
    } else {
      // 后端为空时尝试从 localStorage 迁移旧数据到后端
      loadCustomPresets();
      if (State.customPresets.length > 0) {
        State.customPresets.forEach(p => saveCustomPresetToBackend(p));
      }
    }
    // 初始化默认值
    State.paramGroups.forEach(g => {
      g.params.forEach(p => {
        let dv = p.default;
        if (p.type === "multi" && !Array.isArray(dv)) dv = [];
        if (dv === undefined || dv === null) dv = (p.type === "multi" ? [] : "");
        State.values[p.key] = dv;
      });
    });
  } catch (e) {
    toast("加载参数失败：" + e.message, "err");
  }
}

async function loadVoices() {
  try { State.voices = await apiGet("/api/voices"); }
  catch (e) { console.warn("loadVoices", e); }
}
async function loadFilters() {
  try { State.filters = await apiGet("/api/filters"); }
  catch (e) { console.warn("loadFilters", e); }
}
async function loadEffects() {
  try { State.effects = await apiGet("/api/effects"); }
  catch (e) { console.warn("loadEffects", e); }
}
async function loadAiCovers() {
  try { State.aiCovers = await apiGet("/api/ai-covers"); }
  catch (e) { console.warn("loadAiCovers", e); }
}
async function loadBgm() {
  try { State.bgm = await apiGet("/api/bgm"); }
  catch (e) { console.warn("loadBgm", e); }
}

// 把动态列表填入参数定义的 choices
function applyDynamicChoices() {
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      if (p.dynamic === "voices") {
        p.choices = [];
        if (State.voices.edge) {
          State.voices.edge.forEach(v => p.choices.push({ value: v.alias, label: `${v.alias} — ${v.desc}（edge）` }));
        }
        if (State.voices.volcengine) {
          State.voices.volcengine.forEach(v => p.choices.push({ value: v.alias, label: `${v.alias} — ${v.desc}（火山）` }));
        }
        if (State.voices.elevenlabs) {
          State.voices.elevenlabs.forEach(v => p.choices.push({ value: v.alias, label: `${v.alias} — ${v.desc}（ElevenLabs）` }));
        }
        // 加 auto 选项
        if (!p.choices.find(c => c.value === "auto")) {
          p.choices.unshift({ value: "auto", label: "auto — 根据笔记标签/标题自动选" });
        }
      } else if (p.dynamic === "filters") {
        p.choices = [];
        Object.keys(State.filters).forEach(cat => {
          if (cat === "error") return;
          (State.filters[cat] || []).forEach(f => {
            p.choices.push({ value: f.name, label: `${f.name_cn}（${cat}）${f.has_lut && !f.lut_available ? " ⚠缺LUT" : ""}`, group: cat });
          });
        });
      } else if (p.dynamic === "effects") {
        p.choices = [];
        Object.keys(State.effects).forEach(cat => {
          if (cat === "error") return;
          (State.effects[cat] || []).forEach(eff => {
            p.choices.push({ value: eff.name, label: `${eff.name_cn}（${cat}）${eff.type === "png_overlay" && !eff.png_available ? " ⚠缺素材" : ""}`, group: cat });
          });
        });
      }
    });
  });
}

// ===== 渲染分组 =====
function renderGroups() {
  el.groupTabs.innerHTML = "";
  el.groupPanels.innerHTML = "";
  State.paramGroups.forEach((g, idx) => {
    // Tab
    const tab = document.createElement("button");
    tab.className = "group-tab" + (idx === 0 ? " active" : "");
    tab.type = "button";
    tab.dataset.group = g.key;
    tab.innerHTML = `<span class="tab-icon">${g.icon}</span>${g.title}`;
    tab.addEventListener("click", () => switchGroup(g.key));
    el.groupTabs.appendChild(tab);
    // Panel
    const panel = document.createElement("div");
    panel.className = "group-panel" + (idx === 0 ? " active" : "");
    panel.dataset.group = g.key;
    panel.innerHTML = `<div class="group-desc">${escapeHtml(g.desc)}</div>`;

    if (g.key === "llm") {
      const bar = document.createElement("div");
      bar.className = "llm-action-bar";
      bar.innerHTML = `
        <div class="llm-action-info">
          <span class="llm-action-icon">🔒</span>
          <span>LLM 配置与 API Keys 已支持加密持久化保存（服务端 + 本地文件）</span>
        </div>
        <div class="llm-action-btns">
          <button type="button" class="btn-save-llm" id="btnSaveLlm">💾 保存 LLM 配置</button>
          <button type="button" class="btn-clear-llm" id="btnClearLlm">🗑️ 清除/重置</button>
        </div>
      `;
      panel.appendChild(bar);
      setTimeout(() => {
        const saveBtn = panel.querySelector("#btnSaveLlm");
        const clearBtn = panel.querySelector("#btnClearLlm");
        if (saveBtn) saveBtn.addEventListener("click", manualSaveLlmConfig);
        if (clearBtn) clearBtn.addEventListener("click", manualClearLlmConfig);
      }, 0);
    }

    g.params.forEach(p => panel.appendChild(renderParam(p)));
    el.groupPanels.appendChild(panel);
  });
  if (State.paramGroups.length > 0) {
    State.activeGroup = State.paramGroups[0].key;
  }
}

function switchGroup(key) {
  State.activeGroup = key;
  el.groupTabs.querySelectorAll(".group-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.group === key);
  });
  el.groupPanels.querySelectorAll(".group-panel").forEach(p => {
    p.classList.toggle("active", p.dataset.group === key);
  });
}

// ===== 渲染单个参数 =====
function renderParam(p) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  wrap.dataset.key = p.key;
  wrap.dataset.label = p.label || "";
  wrap.dataset.help = p.help || "";
  wrap.dataset.flag = p.flag || "";

  const head = document.createElement("div");
  head.className = "param-head";
  const labelText = p.label + (p.required ? '<span class="param-required-mark">*</span>' : "");
  head.innerHTML = `<span class="param-label">${labelText}</span>`;
  if (p.flag) {
    head.innerHTML += `<span class="param-flag">${escapeHtml(p.flag)}</span>`;
  }
  wrap.appendChild(head);

  // 帮助文字
  if (p.help) {
    const help = document.createElement("div");
    help.className = "param-help";
    let helpText = p.help;
    if (p.type === "password") {
      helpText = '<span class="security-warn">🔒 持久化安全保护：填写的 API Key/Token 已支持持久化保存（服务端 + 本地文件），下次启动自动恢复</span><br>' + helpText;
    }
    help.innerHTML = helpText;
    wrap.appendChild(help);
  }

  // 控件
  const ctrl = document.createElement("div");
  ctrl.className = "param-control";
  switch (p.type) {
    case "bool":
      ctrl.appendChild(renderSwitch(p));
      break;
    case "int":
    case "float":
      ctrl.appendChild(renderNumber(p));
      break;
    case "choice":
      ctrl.appendChild(renderChoice(p));
      break;
    case "multi":
      ctrl.appendChild(renderMulti(p));
      break;
    case "password":
      ctrl.appendChild(renderPassword(p));
      break;
    default:
      ctrl.appendChild(renderText(p));
  }
  // 动态参数加浏览按钮
  if (p.dynamic === "filters" || p.dynamic === "effects") {
    const btn = document.createElement("button");
    btn.className = "browse-btn";
    btn.type = "button";
    btn.textContent = p.dynamic === "filters" ? "浏览全部" : "添加/浏览";
    btn.addEventListener("click", () => openPicker(p.dynamic, p.key, p.type === "multi"));
    ctrl.appendChild(btn);
  }
  // 配音试听
  if (p.audition) {
    const btn = document.createElement("button");
    btn.className = "audition-btn";
    btn.type = "button";
    btn.textContent = "🎤 试听";
    btn.addEventListener("click", () => openAudition(State.values[p.key]));
    ctrl.appendChild(btn);
  }
  wrap.appendChild(ctrl);
  return wrap;
}

function renderSwitch(p) {
  const label = document.createElement("label");
  label.className = "switch";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!State.values[p.key];
  input.addEventListener("change", () => {
    State.values[p.key] = input.checked;
    onParamChange(p);
  });
  const slider = document.createElement("span");
  slider.className = "slider";
  label.appendChild(input);
  label.appendChild(slider);
  return label;
}

function renderNumber(p) {
  const wrap = document.createDocumentFragment();
  const isFloat = p.type === "float";
  const step = p.step || (isFloat ? 0.01 : 1);
  // 范围滑块（只在有 min/max 时显示）
  if (p.min != null && p.max != null) {
    const range = document.createElement("input");
    range.className = "range-input";
    range.type = "range";
    range.min = p.min;
    range.max = p.max;
    range.step = step;
    // 初始值优先级：当前值 > 默认值 > min > 0
    // 空字符串/null/undefined 都视为"未设置"，用 default 或 min 兜底
    let initVal = State.values[p.key];
    if (initVal === "" || initVal === null || initVal === undefined) {
      initVal = (p.default !== null && p.default !== undefined && p.default !== "")
        ? p.default
        : (p.min != null ? p.min : 0);
    }
    range.value = initVal;
    const valLabel = document.createElement("span");
    valLabel.className = "param-range-val";
    valLabel.textContent = formatNum(range.value, isFloat);
    range.addEventListener("input", () => {
      valLabel.textContent = formatNum(range.value, isFloat);
    });
    range.addEventListener("change", () => {
      State.values[p.key] = isFloat ? parseFloat(range.value) : parseInt(range.value, 10);
      onParamChange(p);
    });
    wrap.appendChild(range);
    wrap.appendChild(valLabel);
  } else {
    // 无 min/max，用纯数字输入框
    const input = document.createElement("input");
    input.className = "input";
    input.type = "number";
    input.step = step;
    input.value = State.values[p.key] != null ? State.values[p.key] : "";
    input.placeholder = p.placeholder || "";
    input.addEventListener("change", () => {
      const v = isFloat ? parseFloat(input.value) : parseInt(input.value, 10);
      State.values[p.key] = isNaN(v) ? null : v;
      onParamChange(p);
    });
    wrap.appendChild(input);
  }
  return wrap;
}

function formatNum(v, isFloat) {
  if (v == null || v === "") return "—";
  return isFloat ? parseFloat(v).toFixed(2) : String(v);
}

function renderChoice(p) {
  const sel = document.createElement("select");
  sel.className = "select";
  // 空选项（表示不传）
  const emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = p.default === null ? "（不指定，用 config 默认）" : "（不指定）";
  sel.appendChild(emptyOpt);
  (p.choices || []).forEach(c => {
    if (c == null) return;  // 跳过 null/undefined（后端 None 序列化为 null）
    const opt = document.createElement("option");
    if (typeof c === "string") {
      opt.value = c;
      opt.textContent = c;
    } else {
      opt.value = c.value;
      opt.textContent = c.label;
    }
    if (State.values[p.key] === opt.value) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => {
    State.values[p.key] = sel.value === "" ? null : sel.value;
    onParamChange(p);
  });
  return sel;
}

function renderMulti(p) {
  const wrap = document.createElement("div");
  wrap.style.flex = "1";
  const box = document.createElement("div");
  box.className = "multiselect";
  const render = () => {
    box.innerHTML = "";
    const arr = State.values[p.key] || [];
    if (arr.length === 0) {
      const empty = document.createElement("span");
      empty.className = "multiselect-empty";
      empty.textContent = "（未选择，可点右侧「浏览全部」添加）";
      box.appendChild(empty);
    } else {
      arr.forEach(v => {
        const tag = document.createElement("span");
        tag.className = "multiselect-tag";
        const label = lookupChoiceLabel(p, v) || v;
        tag.innerHTML = `${escapeHtml(label)} <span class="tag-remove" data-val="${escapeHtml(v)}">×</span>`;
        tag.querySelector(".tag-remove").addEventListener("click", () => {
          State.values[p.key] = arr.filter(x => x !== v);
          onParamChange(p);
          render();
        });
        box.appendChild(tag);
      });
    }
    const addBtn = document.createElement("button");
    addBtn.className = "multiselect-add-btn";
    addBtn.type = "button";
    addBtn.textContent = "+ 添加";
    addBtn.addEventListener("click", () => openPicker(p.dynamic, p.key, true));
    box.appendChild(addBtn);
  };
  render();
  wrap.appendChild(box);
  return wrap;
}

function lookupChoiceLabel(p, value) {
  if (!p.choices) return null;
  for (const c of p.choices) {
    if (typeof c === "string") {
      if (c === value) return c;
    } else if (c.value === value) {
      return c.label;
    }
  }
  return null;
}

function renderPassword(p) {
  const input = document.createElement("input");
  input.className = "input";
  input.type = "password";
  input.value = State.values[p.key] || "";
  input.placeholder = p.placeholder || "";
  input.autocomplete = "off";
  input.addEventListener("change", () => {
    State.values[p.key] = input.value;
    onParamChange(p);
  });
  return input;
}

function renderText(p) {
  const input = document.createElement("input");
  input.className = "input";
  input.type = "text";
  input.value = State.values[p.key] || "";
  input.placeholder = p.placeholder || "";
  input.addEventListener("change", () => {
    State.values[p.key] = input.value;
    onParamChange(p);
  });
  input.addEventListener("input", () => {
    // 实时更新预览（用于 URL 字段）
    State.values[p.key] = input.value;
    scheduleCmdPreview();
  });
  return input;
}

// ===== 参数变更处理（依赖/互斥/必填校验） =====
function onParamChange(p) {
  // 联动：勾选 voice_rewrite 时自动启用 whisper + voice（替代原来的报错拦截）
  if (p && p.key === "voice_rewrite" && State.values.voice_rewrite) {
    const auto = [];
    if (!State.values.whisper) {
      State.values.whisper = true;
      const cb = document.querySelector('.param[data-key="whisper"] input[type="checkbox"]');
      if (cb) cb.checked = true;
      auto.push("whisper 语音识别");
    }
    if (!State.values.voice) {
      State.values.voice = "auto";
      const vi = document.querySelector('.param[data-key="voice"] input[type="text"]');
      if (vi) vi.value = "auto";
      auto.push("AI 配音(auto)");
    }
    if (auto.length) toast("口播二创需 whisper+配音，已自动启用：" + auto.join("、"), "ok");
  }
  updateParamDisabled();
  updateParamConflicts();
  updateRequiredHighlight();
  refreshCmdPreview();
  saveHistory();
  // LLM 相关参数变更时自动持久化到浏览器
  if (p && LLM_PERSIST_KEYS.includes(p.key)) {
    saveLlmConfig();
    // 服务商切换：保存旧服务商配置 + 加载新服务商配置
    if (PROVIDER_FIELDS[p.key] && _prevProvider[p.key] !== State.values[p.key]) {
      handleProviderSwitch(p.key, _prevProvider[p.key], State.values[p.key]);
      _prevProvider[p.key] = State.values[p.key];
    } else {
      // 普通 key 编辑（model/api_key/extra）：同步更新当前服务商的存储
      syncCurrentProviderConfig(p.key);
    }
  }
}

// 依赖检查：根据 depends_on / depends_value / depends_value_any 启用/禁用参数
function updateParamDisabled() {
  // 建立 key -> value 索引
  const vals = State.values;
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      if (!p.depends_on) return;
      const depVal = vals[p.depends_on];
      let ok = false;
      if (p.depends_value_any) {
        ok = p.depends_value_any.includes(depVal);
      } else if (p.depends_value != null) {
        ok = depVal === p.depends_value;
      } else {
        // 仅依赖开关开启（multi 类型需判断数组非空）
        ok = Array.isArray(depVal) ? depVal.length > 0 : !!depVal;
      }
      const node = document.querySelector(`.param[data-key="${p.key}"]`);
      if (node) node.classList.toggle("disabled", !ok);
    });
  });
}

// 互斥检查：wm_ocr 与 remove_wm / wm_replace
function updateParamConflicts() {
  const conflicts = [];
  if (State.values.wm_ocr && State.values.remove_wm) {
    conflicts.push(["wm_ocr", "remove_wm"]);
  }
  if (State.values.wm_ocr && State.values.wm_replace) {
    conflicts.push(["wm_ocr", "wm_replace"]);
  }
  if (State.values.enhance_only && State.values.note) {
    // 这是替代关系，不算冲突，build_command 会处理
  }
  // 清除旧标记
  document.querySelectorAll(".param.conflict").forEach(n => n.classList.remove("conflict"));
  conflicts.forEach(pair => {
    pair.forEach(k => {
      const n = document.querySelector(`.param[data-key="${k}"]`);
      if (n) n.classList.add("conflict");
    });
  });
}

// 必填项高亮（仅高亮当前提交时未填的，这里持续检查 note）
function updateRequiredHighlight() {
  const noteNode = document.querySelector('.param[data-key="note"]');
  const enhanceNode = document.querySelector('.param[data-key="enhance_only"]');
  if (noteNode) noteNode.classList.remove("required-empty");
  if (enhanceNode) enhanceNode.classList.remove("required-empty");
  const hasNote = (State.values.note || "").trim();
  const hasEnhance = (State.values.enhance_only || "").trim();
  if (!hasNote && !hasEnhance) {
    if (noteNode) noteNode.classList.add("required-empty");
  }
}

// ===== 命令预览（节流） =====
let cmdPreviewTimer = null;
function scheduleCmdPreview() {
  clearTimeout(cmdPreviewTimer);
  cmdPreviewTimer = setTimeout(refreshCmdPreview, 200);
}

async function refreshCmdPreview() {
  try {
    const r = await apiPost("/api/preview", { values: State.values });
    renderCmdPreview(r.cmd, r.errors || []);
  } catch (e) {
    el.cmdPreview.innerHTML = `<span class="cmd-empty">预览失败：${escapeHtml(e.message)}</span>`;
  }
}

function renderCmdPreview(cmd, errors) {
  if (!cmd || cmd.length === 0) {
    el.cmdPreview.innerHTML = '<span class="cmd-empty">（参数不完整，无法生成命令）</span>';
  } else {
    // 高亮：第 0 是 python，第 1 是脚本，后面是 flag/value 交替
    const parts = cmd.map((c, i) => {
      if (i === 0) return `<span class="cmd-prog">${escapeHtml(c)}</span>`;
      if (i === 1) return `<span class="cmd-prog">${escapeHtml(c)}</span>`;
      if (c.startsWith("--") || c.startsWith("-")) {
        return `<span class="cmd-flag">${escapeHtml(c)}</span>`;
      }
      return `<span class="cmd-value">${escapeHtml(c)}</span>`;
    });
    el.cmdPreview.innerHTML = parts.join(" ");
  }
  if (errors && errors.length > 0) {
    el.cmdErrors.hidden = false;
    el.cmdErrors.innerHTML = "<ul><li>" + errors.map(escapeHtml).join("</li><li>") + "</li></ul>";
  } else {
    el.cmdErrors.hidden = true;
  }
}

function copyCmd() {
  const text = el.cmdPreview.textContent || "";
  if (!text || text.startsWith("（")) {
    toast("无可复制的命令", "warn");
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => toast("已复制到剪贴板", "ok"),
    () => toast("复制失败，请手动选中复制", "err")
  );
}

// ===== 预设方案 =====
function renderPresetOptions() {
  el.presetSelect.innerHTML = '<option value="">— 选择预设 —</option>';
  // 内置预设
  const builtinGroup = document.createElement("optgroup");
  builtinGroup.label = "内置预设";
  State.presets.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.key;
    opt.textContent = `${p.icon} ${p.name} — ${p.desc}`;
    builtinGroup.appendChild(opt);
  });
  el.presetSelect.appendChild(builtinGroup);
  // 自定义模板
  if (State.customPresets.length > 0) {
    const customGroup = document.createElement("optgroup");
    customGroup.label = "我的模板";
    State.customPresets.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.key;
      opt.textContent = `${p.icon || "📌"} ${p.name}`;
      customGroup.appendChild(opt);
    });
    el.presetSelect.appendChild(customGroup);
  }
}

function onPresetChange(e) {
  const key = e.target.value;
  if (!key) return;
  // 先查内置预设，再查自定义模板
  let preset = State.presets.find(p => p.key === key);
  if (!preset) preset = State.customPresets.find(p => p.key === key);
  if (preset) applyPreset(preset, true);
}

function applyPreset(preset, showToast) {
  if (!preset) return;
  // 保存当前 LLM 配置（预设不应覆盖 LLM 配置）
  const llmBackup = {};
  LLM_PERSIST_KEYS.forEach(k => { llmBackup[k] = State.values[k]; });
  // 不覆盖 note/enhance_only（用户输入源）
  const keepNote = State.values.note;
  const keepEnhance = State.values.enhance_only;
  // 重置所有参数到默认
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      let dv = p.default;
      if (p.type === "multi" && !Array.isArray(dv)) dv = [];
      if (dv === undefined || dv === null) dv = (p.type === "multi" ? [] : "");
      State.values[p.key] = dv;
    });
  });
  // 应用预设值
  Object.assign(State.values, preset.values || {});
  // 恢复输入源和 LLM 配置
  State.values.note = keepNote || "";
  State.values.enhance_only = keepEnhance || "";
  Object.assign(State.values, llmBackup);
  // 重新渲染
  rerenderValues();
  updateParamDisabled();
  updateParamConflicts();
  refreshCmdPreview();
  if (showToast) toast(`已应用预设：${preset.name}`, "ok");
}

// 应用预设但保留 LLM 配置（初始化时用，避免预设覆盖已恢复的 LLM 配置）
function applyPresetKeepLlm(preset, showToast) {
  if (!preset) return;
  const llmBackup = {};
  LLM_PERSIST_KEYS.forEach(k => { llmBackup[k] = State.values[k]; });
  applyPreset(preset, false);
  Object.assign(State.values, llmBackup);
  rerenderValues();
  updateParamDisabled();
  refreshCmdPreview();
  if (showToast) toast(`已应用预设：${preset.name}`, "ok");
}

// ===== 自定义模板（后端持久化 + localStorage 离线缓存） =====
function loadCustomPresets() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.CUSTOM_PRESETS);
    State.customPresets = raw ? JSON.parse(raw) : [];
  } catch (e) {
    console.warn("loadCustomPresets", e);
    State.customPresets = [];
  }
}

function saveCustomPresetsLocal() {
  // 仅写 localStorage 做离线缓存（真正持久化走后端 API）
  try {
    localStorage.setItem(STORAGE_KEYS.CUSTOM_PRESETS, JSON.stringify(State.customPresets));
  } catch (e) {
    console.warn("saveCustomPresetsLocal", e);
  }
}

// 保存单个模板到后端（新增或更新）
async function saveCustomPresetToBackend(preset) {
  try {
    await apiPost("/api/custom-presets", { key: preset.key, name: preset.name, values: preset.values });
  } catch (e) {
    console.warn("saveCustomPresetToBackend", e);
  }
}

function onSaveCustomPreset() {
  // 收集当前参数（排除 note/enhance_only 等输入源）
  const values = {};
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      if (p.key === "note" || p.key === "enhance_only") return;
      const v = State.values[p.key];
      if (p.type === "multi") {
        values[p.key] = Array.isArray(v) ? [...v] : [];
      } else if (v !== null && v !== undefined && v !== "" && v !== false) {
        values[p.key] = v;
      }
    });
  });
  // 用 prompt 输入名称（浏览器原生 prompt）
  const name = window.prompt("请输入模板名称：", `自定义模板 ${State.customPresets.length + 1}`);
  if (!name || !name.trim()) return;
  const key = "custom_" + Date.now();
  const preset = { key, name: name.trim(), icon: "📌", desc: "自定义", values };
  State.customPresets.push(preset);
  saveCustomPresetsLocal();          // 本地缓存（离线兜底）
  saveCustomPresetToBackend(preset); // 后端持久化（真正存储）
  renderPresetOptions();
  el.presetSelect.value = key;
  toast(`已保存模板：${name.trim()}`, "ok");
}

async function onDeleteCustomPreset() {
  const key = el.presetSelect.value;
  if (!key) {
    toast("请先在下拉框中选择要删除的自定义模板", "err");
    return;
  }
  const idx = State.customPresets.findIndex(p => p.key === key);
  if (idx < 0) {
    toast("该预设是内置预设，无法删除", "err");
    return;
  }
  const preset = State.customPresets[idx];
  if (!window.confirm(`确认删除模板「${preset.name}」？`)) return;
  State.customPresets.splice(idx, 1);
  saveCustomPresetsLocal();          // 更新本地缓存
  // 后端删除
  try {
    await apiPost("/api/custom-presets/delete", { key });
  } catch (e) {
    console.warn("后端删除模板失败", e);
  }
  renderPresetOptions();
  el.presetSelect.value = "";
  toast(`已删除模板：${preset.name}`, "ok");
}

// ===== LLM 配置持久化（服务端文件 config/llm_config.json + localStorage 双重保证） =====
let _llmSyncTimer = null;
function syncLlmConfigToBackend(cfg, providerConfigs) {
  if (_llmSyncTimer) clearTimeout(_llmSyncTimer);
  _llmSyncTimer = setTimeout(() => {
    const payload = {
      llm_config: cfg || collectLlmConfigValues(),
      provider_configs: providerConfigs || loadProviderConfigs(),
    };
    apiPost("/api/llm-config", payload).catch(e => {
      console.warn("同步 LLM 配置到后端失败:", e);
    });
  }, 300);
}

function collectLlmConfigValues() {
  const cfg = {};
  LLM_PERSIST_KEYS.forEach(k => {
    cfg[k] = State.values[k];
  });
  return cfg;
}

function loadLlmConfig() {
  try {
    let cfg = null;
    let providerConfigs = null;

    // 优先从后端 API 返回的数据还原
    if (State.backendLlmConfig) {
      if (State.backendLlmConfig.llm_config && Object.keys(State.backendLlmConfig.llm_config).length > 0) {
        cfg = State.backendLlmConfig.llm_config;
      }
      if (State.backendLlmConfig.provider_configs && Object.keys(State.backendLlmConfig.provider_configs).length > 0) {
        providerConfigs = State.backendLlmConfig.provider_configs;
      }
    }

    // 兜底从 localStorage 读取
    if (!cfg) {
      const raw = localStorage.getItem(STORAGE_KEYS.LLM_CONFIG);
      if (raw) cfg = JSON.parse(raw);
    }
    if (!providerConfigs) {
      const rawP = localStorage.getItem(STORAGE_KEYS.PROVIDER_CONFIGS);
      if (rawP) providerConfigs = JSON.parse(rawP);
    }

    if (cfg) {
      LLM_PERSIST_KEYS.forEach(k => {
        if (cfg[k] !== undefined && cfg[k] !== null) {
          State.values[k] = cfg[k];
        }
      });
      try { localStorage.setItem(STORAGE_KEYS.LLM_CONFIG, JSON.stringify(cfg)); } catch (_) {}
    }

    if (providerConfigs) {
      try { localStorage.setItem(STORAGE_KEYS.PROVIDER_CONFIGS, JSON.stringify(providerConfigs)); } catch (_) {}
    }
  } catch (e) {
    console.warn("loadLlmConfig", e);
  }
}

function saveLlmConfig() {
  try {
    const cfg = collectLlmConfigValues();
    localStorage.setItem(STORAGE_KEYS.LLM_CONFIG, JSON.stringify(cfg));
    syncLlmConfigToBackend(cfg, loadProviderConfigs());
  } catch (e) {
    console.warn("saveLlmConfig", e);
  }
}

async function manualSaveLlmConfig() {
  try {
    const cfg = collectLlmConfigValues();
    const providerConfigs = loadProviderConfigs();
    localStorage.setItem(STORAGE_KEYS.LLM_CONFIG, JSON.stringify(cfg));
    await apiPost("/api/llm-config", { llm_config: cfg, provider_configs: providerConfigs });
    toast("✅ LLM 配置与 API Keys 已成功保存（服务端 + 本地文件，下次启动自动恢复）", "ok");
  } catch (e) {
    toast("保存 LLM 配置失败：" + e.message, "err");
  }
}

async function manualClearLlmConfig() {
  if (!confirm("确认重置并清除已保存的 LLM 配置与 API Keys 吗？")) return;
  try {
    localStorage.removeItem(STORAGE_KEYS.LLM_CONFIG);
    localStorage.removeItem(STORAGE_KEYS.PROVIDER_CONFIGS);
    await apiPost("/api/llm-config/delete", {});
  } catch (e) {
    console.warn("清除服务端 LLM 配置失败:", e);
  }

  LLM_PERSIST_KEYS.forEach(k => {
    let dv = "";
    State.paramGroups.forEach(g => {
      g.params.forEach(p => {
        if (p.key === k) {
          dv = (p.default !== null && p.default !== undefined ? p.default : "");
        }
      });
    });
    State.values[k] = dv;
  });
  rerenderValues();
  updateParamDisabled();
  refreshCmdPreview();
  toast("已重置并清除 LLM 配置与 API Keys", "ok");
}

// ===== 按服务商持久化 key/token（切换服务商自动带出） =====
function loadProviderConfigs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.PROVIDER_CONFIGS);
    return raw ? JSON.parse(raw) : {};
  } catch (e) { return {}; }
}

function saveProviderConfigs(all) {
  try {
    localStorage.setItem(STORAGE_KEYS.PROVIDER_CONFIGS, JSON.stringify(all || {}));
    syncLlmConfigToBackend(null, all);
  } catch (e) { console.warn("saveProviderConfigs", e); }
}

function getProviderConfig(scope, provider) {
  if (!provider) return null;
  const all = loadProviderConfigs();
  return (all[scope] || {})[provider] || null;
}

function setProviderConfig(scope, provider, data) {
  if (!provider) return;
  const all = loadProviderConfigs();
  if (!all[scope]) all[scope] = {};
  all[scope][provider] = data;
  saveProviderConfigs(all);
}

// 收集当前服务商的字段值
function collectProviderData(meta) {
  const data = {};
  if (meta.modelKey) data.model = State.values[meta.modelKey] || "";
  if (meta.apiKeyKey) data.api_key = State.values[meta.apiKeyKey] || "";
  Object.keys(meta.extra || {}).forEach(stateKey => {
    data[meta.extra[stateKey]] = State.values[stateKey] || "";
  });
  return data;
}

// 把 saved 数据写回 State.values 和对应输入框
function applyProviderData(meta, saved) {
  if (!saved) return;
  if (meta.modelKey && saved.model !== undefined) {
    State.values[meta.modelKey] = saved.model || "";
    const inp = document.querySelector(`.param[data-key="${meta.modelKey}"] input`);
    if (inp) inp.value = saved.model || "";
  }
  if (meta.apiKeyKey && saved.api_key !== undefined) {
    State.values[meta.apiKeyKey] = saved.api_key || "";
    const inp = document.querySelector(`.param[data-key="${meta.apiKeyKey}"] input`);
    if (inp) inp.value = saved.api_key || "";
  }
  Object.keys(meta.extra || {}).forEach(stateKey => {
    const storedKey = meta.extra[stateKey];
    if (saved[storedKey] !== undefined) {
      State.values[stateKey] = saved[storedKey] || "";
      const inp = document.querySelector(`.param[data-key="${stateKey}"] input`);
      if (inp) inp.value = saved[storedKey] || "";
    }
  });
}

// 清空当前服务商的 model/api_key 输入（用于首次切换到没有存过的服务商）
function clearProviderInputs(meta) {
  if (meta.apiKeyKey) {
    State.values[meta.apiKeyKey] = "";
    const inp = document.querySelector(`.param[data-key="${meta.apiKeyKey}"] input`);
    if (inp) inp.value = "";
  }
  Object.keys(meta.extra || {}).forEach(stateKey => {
    State.values[stateKey] = "";
    const inp = document.querySelector(`.param[data-key="${stateKey}"] input`);
    if (inp) inp.value = "";
  });
  // model 留空让其用服务商默认（不主动清空，避免清掉用户想保留的模型名）
}

// 服务商切换处理：保存旧服务商 → 加载新服务商
function handleProviderSwitch(paramKey, oldProvider, newProvider) {
  const meta = PROVIDER_FIELDS[paramKey];
  // 1. 保存旧服务商的当前 model/api_key（此时 State.values 里还是旧服务商的 key）
  if (oldProvider) {
    setProviderConfig(meta.scope, oldProvider, collectProviderData(meta));
  }
  // 2. 加载新服务商的已存配置
  if (newProvider) {
    const saved = getProviderConfig(meta.scope, newProvider);
    if (saved) {
      applyProviderData(meta, saved);
      toast(`已带出 ${newProvider} 的已存配置`, "ok");
    } else {
      clearProviderInputs(meta);
      toast(`首次使用 ${newProvider}，请填写对应的 API Key/Token`, "");
    }
  }
  refreshCmdPreview();
}

// 普通 key 编辑（非服务商切换）：同步更新当前服务商的存储
function syncCurrentProviderConfig(paramKey) {
  for (const [provKey, meta] of Object.entries(PROVIDER_FIELDS)) {
    const belongs = (provKey === paramKey)
      || (meta.modelKey === paramKey)
      || (meta.apiKeyKey === paramKey)
      || (meta.extra && meta.extra[paramKey]);
    if (belongs) {
      const provider = State.values[provKey];
      if (provider) setProviderConfig(meta.scope, provider, collectProviderData(meta));
      return;
    }
  }
}

// 初始化 _prevProvider（在 loadLlmConfig 之后调用）
function initPrevProviders() {
  Object.keys(PROVIDER_FIELDS).forEach(k => {
    _prevProvider[k] = State.values[k] || "";
  });
}

function clearLlmConfig() {
  localStorage.removeItem(STORAGE_KEYS.LLM_CONFIG);
  localStorage.removeItem(STORAGE_KEYS.PROVIDER_CONFIGS);
  toast("已清除浏览器保存的 LLM 配置（含各服务商 key/token）", "ok");
}

function rerenderValues() {
  // 重新渲染所有参数控件（保持当前值）
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      const node = document.querySelector(`.param[data-key="${p.key}"]`);
      if (!node) return;
      const ctrl = node.querySelector(".param-control");
      if (!ctrl) return;
      ctrl.innerHTML = "";
      switch (p.type) {
        case "bool": ctrl.appendChild(renderSwitch(p)); break;
        case "int":
        case "float": {
          const frag = renderNumber(p);
          if (frag.nodeType) ctrl.appendChild(frag);
          else ctrl.appendChild(frag);
          break;
        }
        case "choice": ctrl.appendChild(renderChoice(p)); break;
        case "multi": ctrl.appendChild(renderMulti(p)); break;
        case "password": ctrl.appendChild(renderPassword(p)); break;
        default: ctrl.appendChild(renderText(p));
      }
      if (p.dynamic === "filters" || p.dynamic === "effects") {
        const btn = document.createElement("button");
        btn.className = "browse-btn";
        btn.type = "button";
        btn.textContent = p.dynamic === "filters" ? "浏览全部" : "添加/浏览";
        btn.addEventListener("click", () => openPicker(p.dynamic, p.key, p.type === "multi"));
        ctrl.appendChild(btn);
      }
      if (p.audition) {
        const btn = document.createElement("button");
        btn.className = "audition-btn";
        btn.type = "button";
        btn.textContent = "🎤 试听";
        btn.addEventListener("click", () => openAudition(State.values[p.key]));
        ctrl.appendChild(btn);
      }
    });
  });
}

// ===== 重置 =====
function onReset() {
  if (!confirm("确定清空所有非必填参数？")) return;
  State.paramGroups.forEach(g => {
    g.params.forEach(p => {
      let dv = p.default;
      if (p.type === "multi" && !Array.isArray(dv)) dv = [];
      if (dv === undefined || dv === null) dv = (p.type === "multi" ? [] : "");
      if (p.key !== "note" && p.key !== "enhance_only") {
        State.values[p.key] = dv;
      }
    });
  });
  rerenderValues();
  updateParamDisabled();
  updateParamConflicts();
  refreshCmdPreview();
  toast("已重置参数", "ok");
}

// ===== 搜索 =====
function onSearch() {
  const q = (el.paramSearch.value || "").trim().toLowerCase();
  el.clearSearch.hidden = !q;
  if (!q) {
    // 恢复当前分组显示
    document.querySelectorAll(".group-panel").forEach(p => {
      p.querySelectorAll(".param").forEach(n => n.style.display = "");
    });
    return;
  }
  // 跨分组搜索：展开所有分组，匹配的显示，不匹配的隐藏
  // 切换到 "搜索结果" 视图：把所有 panel 设为 active
  document.querySelectorAll(".group-panel").forEach(p => p.classList.add("active"));
  document.querySelectorAll(".param").forEach(n => {
    const text = (n.dataset.label + " " + n.dataset.help + " " + n.dataset.flag).toLowerCase();
    n.style.display = text.includes(q) ? "" : "none";
  });
}

// ===== 运行 / 停止 =====
async function onRun() {
  // 校验必填
  const note = (State.values.note || "").trim();
  const enhance = (State.values.enhance_only || "").trim();
  if (!note && !enhance) {
    toast("请填写「笔记 URL / 本地文件」或「仅增强模式」路径", "err");
    document.querySelector('.param[data-key="note"]')?.classList.add("required-empty");
    switchGroup("input");
    return;
  }
  // 互斥校验
  if (State.values.wm_ocr && State.values.remove_wm) {
    toast("OCR 模式与颜色检测模式互斥，请只选一个", "err");
    return;
  }
  if (State.values.wm_ocr && State.values.wm_replace) {
    toast("「替换文字」已自动启用 OCR，无需再勾「OCR 模式」，请取消勾选", "err");
    return;
  }
  // voice_rewrite 的依赖已在 onParamChange 中自动联动启用，不再拦截
  // 提交
  setRunning(true);
  clearLog();
  el.outputsCard.hidden = true;
  try {
    const r = await apiPost("/api/run", { values: State.values });
    if (!r.ok) {
      toast(r.errors ? r.errors.join("；") : "启动失败", "err");
      setRunning(false);
      return;
    }
    toast("任务已启动", "ok");
    State.taskStartTime = Date.now();
    startProgress();
    startSSE();
  } catch (e) {
    toast("启动失败：" + e.message, "err");
    setRunning(false);
  }
}

async function onStop() {
  try {
    const r = await apiPost("/api/stop", {});
    toast(r.ok ? "已发送停止信号" : r.msg || "停止失败", r.ok ? "warn" : "err");
  } catch (e) {
    toast("停止失败：" + e.message, "err");
  }
}

function setRunning(running) {
  State.running = running;
  el.runBtn.disabled = running;
  el.stopBtn.disabled = !running;
  el.runBarHint.textContent = running ? "任务运行中…" : "填写 URL 后点击运行";
  el.runBarHint.classList.toggle("running", running);
}

// ===== SSE 实时日志 =====
function startSSE() {
  if (State.sseSource) {
    try { State.sseSource.close(); } catch (_) {}
  }
  const url = `/api/run/status?since=${State.logSince}`;
  const src = new EventSource(url);
  State.sseSource = src;
  src.addEventListener("log", (e) => {
    const data = JSON.parse(e.data);
    appendLog(data);
    State.logSince += 1;
  });
  src.addEventListener("status", (e) => {
    const data = JSON.parse(e.data);
    updateProgressStatus(data.status);
  });
  src.addEventListener("end", (e) => {
    const data = JSON.parse(e.data);
    onTaskEnd(data.status, data.return_code);
    src.close();
    State.sseSource = null;
  });
  src.addEventListener("outputs", (e) => {
    try {
      const data = JSON.parse(e.data);
      renderOutputs(data);
    } catch (_) {}
  });
  src.addEventListener("ping", () => {});
  src.onerror = () => {
    // SSE 连接断开（浏览器切后台/休眠/网络抖动）
    // 主动关闭，避免 EventSource 自动重连用旧 since 值
    try { src.close(); } catch (_) {}
    State.sseSource = null;
    if (State.running) {
      // 3s 后重连（checkStatus 会确认仍在 running 再 startSSE）
      setTimeout(() => {
        if (State.running) checkStatus();
      }, 3000);
    }
  };
}

function appendLog(entry) {
  const line = document.createElement("div");
  let cls = "log-line " + (entry.level || "info");
  if (entry.stage) cls += " stage";
  line.className = cls;
  const time = formatTime(entry.t);
  let tag = "";
  if (entry.stage) {
    const stageInfo = STAGES.find(s => s.key === entry.stage);
    if (stageInfo) tag = `<span class="log-tag">${stageInfo.icon} ${stageInfo.label}</span>`;
  }
  line.innerHTML = `<span class="log-time">${time}</span>${tag}${escapeHtml(entry.msg)}`;
  el.logBox.appendChild(line);
  // 限制日志条数
  while (el.logBox.children.length > 2000) {
    el.logBox.removeChild(el.logBox.firstChild);
  }
  // 自动滚动
  if (el.autoscrollChk.checked) {
    el.logBox.scrollTop = el.logBox.scrollHeight;
  }
  // 阶段更新
  if (entry.stage) updateProgressStage(entry.stage);
}

function clearLog() {
  el.logBox.innerHTML = "";
  State.logSince = 0;
}

// ===== 进度 =====
function startProgress() {
  el.progressCard.hidden = false;
  el.progressFill.style.width = "0%";
  el.progressStage.textContent = "准备中…";
  el.progressElapsed.textContent = "00:00";
  renderProgressSteps(null);
  if (State.progressTimer) clearInterval(State.progressTimer);
  State.progressTimer = setInterval(() => {
    if (!State.taskStartTime) return;
    const elapsed = Date.now() - State.taskStartTime;
    el.progressElapsed.textContent = formatElapsed(elapsed);
  }, 500);
}

function stopProgress() {
  if (State.progressTimer) {
    clearInterval(State.progressTimer);
    State.progressTimer = null;
  }
}

function updateProgressStage(stage) {
  const info = STAGES.find(s => s.key === stage);
  if (info) {
    el.progressStage.textContent = `${info.icon} ${info.label}`;
    // 进度估算：按阶段索引
    const idx = STAGE_INDEX[stage] || 0;
    const pct = Math.min(95, ((idx + 1) / STAGES.length) * 100);
    el.progressFill.style.width = pct + "%";
    renderProgressSteps(stage);
  }
}

function updateProgressStatus(status) {
  if (status === "running") {
    // 还在跑，不动
  } else if (status === "done") {
    el.progressStage.textContent = "✅ 完成";
    el.progressFill.style.width = "100%";
  } else if (status === "error") {
    el.progressStage.textContent = "❌ 出错";
  } else if (status === "stopped") {
    el.progressStage.textContent = "⏹ 已停止";
  }
}

function renderProgressSteps(activeStage) {
  const activeIdx = activeStage ? STAGE_INDEX[activeStage] : -1;
  el.progressSteps.innerHTML = STAGES.map((s, i) => {
    let cls = "progress-step";
    if (activeStage === null) {
      // nothing yet
    } else if (i < activeIdx) {
      cls += " done";
    } else if (i === activeIdx) {
      cls += " active";
    }
    return `<span class="${cls}">${s.icon} ${s.label}</span>`;
  }).join("");
}

function onTaskEnd(status, returnCode) {
  stopProgress();
  setRunning(false);
  if (status === "done") {
    el.progressStage.textContent = "✅ 完成";
    el.progressFill.style.width = "100%";
    toast("任务完成", "ok");
  } else if (status === "error") {
    el.progressStage.textContent = `❌ 出错（返回码 ${returnCode}）`;
    toast("任务出错，请查看日志", "err");
  } else if (status === "stopped") {
    el.progressStage.textContent = "⏹ 已停止";
    toast("任务已停止", "warn");
  }
  // 主动刷新产出
  refreshOutputs();
  saveHistory();
}

// ===== 状态检查（页面打开时） =====
async function checkStatus() {
  try {
    const r = await apiGet("/api/status");
    if (r.status === "running") {
      setRunning(true);
      State.taskStartTime = r.started_at ? r.started_at * 1000 : Date.now();
      startProgress();
      // SSE 断线重连：用后端已有日志数作为 since，避免漏日志
      State.logSince = r.log_count || State.logSince;
      startSSE();
      toast("检测到正在运行的任务，已恢复日志流", "warn");
    }
  } catch (_) {}
}

// 页面从后台切回前台时，立即检查并重连 SSE
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && State.running && !State.sseSource) {
    checkStatus();
  }
});

// ===== 产出文件 =====
async function refreshOutputs() {
  try {
    const r = await apiGet("/api/status");
    // 直接扫描产出（通过 SSE 推送的 outputs 已渲染；这里手动刷新则调用 /api/run/status 不合适）
    // 改为通过一个独立调用：复用 SSE 的 outputs 事件不合适，这里直接 fetch 一个轻量接口
    // 后端没有独立扫描接口，复用 /api/run/status 但 since=很大 跳过日志
    const resp = await fetch(`/api/run/status?since=${r.log_count || 999999}`);
    // 这是 SSE 流，无法直接读，这里改用另一种方式：直接发起一个轻量请求
    // 由于后端没有独立接口，我们手动解析已收到的 outputs（SSE 推送）
    // 这里仅做兜底：如果 outputsCard 已显示，不重复
    if (!el.outputsCard.hidden) return;
    resp.body?.cancel();
  } catch (_) {}
}

function renderOutputs(data) {
  const dirs = data.dirs || [];
  if (dirs.length === 0) {
    el.outputsCard.hidden = false;
    el.outputsList.innerHTML = '<div class="output-dir"><div class="output-dir-head">📂 暂无产出文件</div></div>';
    return;
  }
  el.outputsCard.hidden = false;
  el.outputsList.innerHTML = dirs.map(d => {
    const files = (d.files || []).map(f => {
      const sizeStr = formatSize(f.size);
      return `<div class="output-file">
        <span class="output-file-name"><span class="file-icon">${fileIcon(f.name)}</span>${escapeHtml(f.name)}</span>
        <span class="output-file-meta">
          <span>${sizeStr}</span>
          <button class="output-file-open" data-path="${escapeHtml(f.path)}">打开</button>
        </span>
      </div>`;
    }).join("");
    return `<div class="output-dir">
      <div class="output-dir-head">📁 ${escapeHtml(d.dir)}</div>
      <div class="output-files">${files || '<div style="color:var(--text-mute);font-size:12px;padding:4px 8px">空目录</div>'}</div>
    </div>`;
  }).join("");
  // 绑定打开按钮
  el.outputsList.querySelectorAll(".output-file-open").forEach(btn => {
    btn.addEventListener("click", async () => {
      const path = btn.dataset.path;
      try {
        const r = await apiPost("/api/open-file", { path });
        if (!r.ok) toast(r.msg || "打开失败", "err");
      } catch (e) { toast("打开失败：" + e.message, "err"); }
    });
  });
}

// ===== 配音试听弹窗 =====
function openAudition(currentVoice) {
  // 填充音色下拉
  el.auditionVoice.innerHTML = "";
  if (State.voices.edge && State.voices.edge.length > 0) {
    const group = document.createElement("optgroup");
    group.label = "edge（免费，可试听）";
    State.voices.edge.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.alias;
      opt.textContent = `${v.alias} — ${v.desc}`;
      group.appendChild(opt);
    });
    el.auditionVoice.appendChild(group);
  }
  if (State.voices.volcengine && State.voices.volcengine.length > 0) {
    const group = document.createElement("optgroup");
    group.label = "火山引擎（需 API Key，不可试听）";
    State.voices.volcengine.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.alias;
      opt.textContent = `${v.alias} — ${v.desc}`;
      group.appendChild(opt);
    });
    el.auditionVoice.appendChild(group);
  }
  if (State.voices.elevenlabs && State.voices.elevenlabs.length > 0) {
    const group = document.createElement("optgroup");
    group.label = "ElevenLabs（超真实/S2S变声）";
    State.voices.elevenlabs.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.alias;
      opt.textContent = `${v.alias} — ${v.desc}`;
      group.appendChild(opt);
    });
    el.auditionVoice.appendChild(group);
  }
  if (currentVoice) el.auditionVoice.value = currentVoice;
  el.auditionAudio.hidden = true;
  el.auditionHint.textContent = "";
  openModal(el.voiceModal);
}

async function onAuditionPlay() {
  const voice = el.auditionVoice.value;
  if (!voice) {
    toast("请选择音色", "warn");
    return;
  }
  if (voice.startsWith("bd_")) {
    el.auditionHint.textContent = "火山引擎音色不支持免费试听";
    toast("火山引擎音色需 API Key，不支持免费试听", "warn");
    return;
  }
  el.auditionHint.textContent = "生成中…";
  el.auditionPlayBtn.disabled = true;
  try {
    const r = await apiPost("/api/voice-preview", {
      voice,
      rate: el.auditionRate.value || "+0%",
      pitch: el.auditionPitch.value || "+0Hz",
      text: el.auditionText.value || "",
    });
    if (r.ok) {
      el.auditionAudio.src = r.url + "?t=" + Date.now();
      el.auditionAudio.hidden = false;
      el.auditionAudio.play().catch(() => {});
      el.auditionHint.textContent = "试听已生成";
    } else {
      el.auditionHint.textContent = r.msg || "生成失败";
      toast(r.msg || "试听生成失败", "err");
    }
  } catch (e) {
    el.auditionHint.textContent = "失败：" + e.message;
    toast("试听失败：" + e.message, "err");
  } finally {
    el.auditionPlayBtn.disabled = false;
  }
}

// ===== 滤镜/特效选择弹窗 =====
function openPicker(kind, paramKey, multi) {
  el.pickerTitle.textContent = kind === "filters" ? "🎨 选择滤镜" : "✨ 选择特效（可多选）";
  el.pickerBody.innerHTML = "";
  const data = kind === "filters" ? State.filters : State.effects;
  const currentVal = State.values[paramKey];
  const currentArr = multi ? (Array.isArray(currentVal) ? currentVal : []) : null;
  Object.keys(data).forEach(cat => {
    if (cat === "error") return;
    const items = data[cat] || [];
    if (items.length === 0) return;
    const title = document.createElement("div");
    title.className = "picker-section-title";
    title.textContent = cat;
    el.pickerBody.appendChild(title);
    const grid = document.createElement("div");
    grid.className = "picker-grid";
    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "picker-item";
      const isSelected = multi ? currentArr.includes(item.name) : (currentVal === item.name);
      if (isSelected) card.classList.add("selected");
      let tagHtml = "";
      if (kind === "filters") {
        if (item.has_lut) {
          tagHtml = item.lut_available
            ? '<span class="picker-item-tag available">LUT ✓</span>'
            : '<span class="picker-item-tag missing">缺 LUT</span>';
        }
      } else {
        if (item.type === "png_overlay") {
          tagHtml = item.png_available
            ? '<span class="picker-item-tag available">素材 ✓</span>'
            : '<span class="picker-item-tag missing">缺素材</span>';
        }
      }
      card.innerHTML = `
        <div class="picker-item-head">
          <span class="picker-item-name">${escapeHtml(item.name_cn || item.name)}</span>
          ${tagHtml}
        </div>
        <div class="picker-item-desc">${escapeHtml(item.desc || "")}</div>
        <div class="picker-item-code">${escapeHtml(item.name)}</div>
      `;
      card.addEventListener("click", () => {
        if (multi) {
          const arr = State.values[paramKey] || [];
          const idx = arr.indexOf(item.name);
          if (idx >= 0) {
            arr.splice(idx, 1);
            card.classList.remove("selected");
          } else {
            arr.push(item.name);
            card.classList.add("selected");
          }
          State.values[paramKey] = arr;
        } else {
          // 单选：清除其他选中，选中当前
          grid.querySelectorAll(".picker-item").forEach(n => n.classList.remove("selected"));
          card.classList.add("selected");
          State.values[paramKey] = item.name;
        }
        // 同步 UI 控件
        syncParamControl(paramKey);
        refreshCmdPreview();
        saveHistory();
        if (!multi) {
          // 单选选完即关
          setTimeout(() => el.pickerModal.hidden = true, 200);
        }
      });
      grid.appendChild(card);
    });
    el.pickerBody.appendChild(grid);
  });
  openModal(el.pickerModal);
}

// 同步参数控件显示
function syncParamControl(paramKey) {
  const p = findParam(paramKey);
  if (!p) return;
  const node = document.querySelector(`.param[data-key="${paramKey}"]`);
  if (!node) return;
  const ctrl = node.querySelector(".param-control");
  if (!ctrl) return;
  ctrl.innerHTML = "";
  switch (p.type) {
    case "choice": ctrl.appendChild(renderChoice(p)); break;
    case "multi": ctrl.appendChild(renderMulti(p)); break;
  }
  if (p.dynamic === "filters" || p.dynamic === "effects") {
    const btn = document.createElement("button");
    btn.className = "browse-btn";
    btn.type = "button";
    btn.textContent = p.dynamic === "filters" ? "浏览全部" : "添加/浏览";
    btn.addEventListener("click", () => openPicker(p.dynamic, p.key, p.type === "multi"));
    ctrl.appendChild(btn);
  }
}

function findParam(key) {
  for (const g of State.paramGroups) {
    for (const p of g.params) {
      if (p.key === key) return p;
    }
  }
  return null;
}

// ===== 弹窗通用 =====
function openModal(modal) {
  modal.hidden = false;
}

// ===== 历史记录（localStorage） =====
function saveHistory() {
  // 只在非运行时保存
  // 保存最近 20 条参数快照（不含 note 内容长度限制）
  try {
    const snap = {
      t: Date.now(),
      values: JSON.parse(JSON.stringify(State.values)),
    };
    // 截断超长字段
    if (snap.values.note && snap.values.note.length > 200) {
      snap.values.note = snap.values.note.substring(0, 200) + "…";
    }
    State.history.unshift(snap);
    if (State.history.length > 20) State.history = State.history.slice(0, 20);
    localStorage.setItem("xhs_history", JSON.stringify(State.history));
  } catch (_) {}
}

function loadHistory() {
  try {
    const raw = localStorage.getItem("xhs_history");
    if (raw) State.history = JSON.parse(raw) || [];
  } catch (_) {}
}

// ===== 启动 =====
document.addEventListener("DOMContentLoaded", init);
