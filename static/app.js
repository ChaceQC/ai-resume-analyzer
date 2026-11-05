"use strict";

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
let inputMode = "upload";
let currentReport = null;
let aiAvailable = false;
let busy = false;
let toastTimer;
let droppedFile = null;

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
}

function setMode(mode) {
  inputMode = mode;
  for (const key of ["upload", "text"]) {
    const selected = mode === key;
    $(`${key}-panel`).hidden = !selected;
    $(`${key}-tab`).classList.toggle("selected", selected);
    $(`${key}-tab`).setAttribute("aria-selected", String(selected));
  }
}
$("upload-tab").addEventListener("click", () => setMode("upload"));
$("text-tab").addEventListener("click", () => setMode("text"));
$("resume-text").addEventListener("input", () => {
  $("character-count").textContent = $("resume-text").value.length.toLocaleString();
});

function updateFile() {
  const file = droppedFile || $("resume-file").files[0];
  $("file-label").textContent = file ? file.name : "点击上传，或将文件拖到这里";
  $("file-hint").textContent = file ? "文件已选择，准备好后开始分析" : "支持 PDF、DOCX、TXT · 最大 8 MB";
  $("file-actions").hidden = !file;
  $("file-size").textContent = file ? `${(file.size / 1024).toFixed(1)} KB` : "";
}
$("resume-file").addEventListener("change", () => { droppedFile = null; updateFile(); });
$("clear-file").addEventListener("click", () => { droppedFile = null; $("resume-file").value = ""; updateFile(); });
for (const eventName of ["dragenter", "dragover"]) {
  $("drop-zone").addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!busy) $("drop-zone").classList.add("dragover");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  $("drop-zone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("drop-zone").classList.remove("dragover");
  });
}
$("drop-zone").addEventListener("drop", (event) => {
  if (busy || !event.dataTransfer.files.length) return;
  droppedFile = event.dataTransfer.files[0];
  updateFile();
});

async function loadSample() {
  if (busy) return;
  try {
    const response = await fetch("/api/sample");
    if (!response.ok) throw new Error("示例暂时无法加载，请稍后重试。");
    const sample = await response.json();
    $("resume-text").value = sample.resume_text;
    $("job-description").value = sample.job_description;
    $("target-industry").value = "software";
    $("target-role").value = "software";
    $("target-level").value = "junior";
    $("target-city").value = "中国";
    refreshMarketCoverage();
    $("character-count").textContent = sample.resume_text.length.toLocaleString();
    setMode("text");
    currentReport = null;
    $("report").hidden = true;
    $("empty-state").hidden = false;
    $("error-message").hidden = true;
    toast("示例已填入，点击“开始分析简历”查看结果");
    if (window.innerWidth < 930) $("analysis-form").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message); }
}
$("sample-button").addEventListener("click", loadSample);
$("sidebar-sample").addEventListener("click", loadSample);
function closeGuide() {
  const dialog = $("guide-dialog");
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}
$("guide-button").addEventListener("click", () => {
  const dialog = $("guide-dialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
});
$("guide-dialog").querySelector("form").addEventListener("submit", (event) => { event.preventDefault(); closeGuide(); });
$("guide-dialog").addEventListener("click", (event) => {
  if (event.target === $("guide-dialog")) {
    const bounds = $("guide-dialog").getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeGuide();
  }
});
$("use-ai").addEventListener("change", () => { $("ai-privacy").hidden = !$("use-ai").checked; });

function setBusy(value) {
  busy = value;
  for (const element of $("analysis-form").querySelectorAll("input, textarea, select, button")) element.disabled = value;
  $("use-ai").disabled = value || !aiAvailable;
  $("sample-button").disabled = value;
  $("sidebar-sample").disabled = value;
  $("analyze-label").textContent = value ? "正在分析，请稍候…" : "开始分析简历";
  $("loading-state").hidden = !value;
}

function renderChips(id, values, className, emptyText) {
  const container = $(id);
  container.replaceChildren();
  if (!values.length) container.append(node("span", emptyText, "empty-chips"));
  for (const value of values) container.append(node("span", value, `chip ${className}`));
}

function openEvidence(reference) {
  const full = currentReport?.source_text?.[reference.source];
  const characters = typeof full === "string" ? Array.from(full) : [];
  const container = $("evidence-text");
  container.replaceChildren();
  const valid = typeof full === "string" && characters.slice(reference.start, reference.end).join("") === reference.quote;
  if (valid) {
    container.append(node("span", characters.slice(Math.max(0, reference.start - 220), reference.start).join("")),
      node("mark", reference.quote), node("span", characters.slice(reference.end, reference.end + 220).join("")));
  } else container.append(node("mark", reference.quote));
  const line = valid ? characters.slice(0, reference.start).join("").split("\n").length : null;
  $("evidence-location").textContent = `${reference.source === "job" ? "岗位描述" : "简历正文"}${line ? ` · 第 ${line} 行` : ""}`;
  const dialog = $("evidence-dialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}
$("evidence-dialog").querySelector("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const dialog = $("evidence-dialog");
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
});

function criterion(item) {
  const container = node("div", undefined, "score-item");
  container.id = `criterion-${item.id}`;
  const title = node("div", undefined, "criterion-title");
  title.append(node("strong", item.title), node("span", `${item.points} / ${item.max_points} 分`));
  container.append(title, node("p", item.rule, "criterion-rule"));
  if (item.observation) container.append(node("p", item.observation, "criterion-status"));
  if (item.priority) container.append(node("p", `${item.priority}要求 · ${item.status}`, "criterion-status"));
  if (item.missing) container.append(node("p", item.missing, "criterion-missing"));
  for (const reference of [...(item.evidence || []), ...(item.resume_evidence || [])]) {
    const quote = node("blockquote", reference.quote, "evidence-quote");
    const button = node("button", reference.source === "job" ? "定位岗位原文" : "定位简历原文", "evidence-button");
    button.type = "button";
    button.addEventListener("click", () => openEvidence(reference));
    container.append(quote, button);
  }
  if (item.sources?.length) {
    const details = node("details", undefined, "market-sources");
    details.append(node("summary", `${item.company_mentions}/${item.company_total} 家企业提出该要求 · 查看 ${item.sources.length} 条职位依据`));
    for (const source of item.sources) {
      const box = node("div", undefined, "market-source");
      const link = node("a", `${source.company} · ${source.title}`, "text-link");
      try {
        const url = new URL(source.url);
        if (url.protocol === "https:") { link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; }
      } catch {}
      box.append(link, node("p", `${source.location} · 更新于 ${source.updated_at.slice(0, 10)}`, "section-note"), node("blockquote", source.quote, "evidence-quote"));
      details.append(box);
    }
    container.append(details);
  }
  return container;
}

function renderScoring(report) {
  const items = report.score_items || [];
  $("score-ledger-total").textContent = `${items.reduce((total, item) => total + item.points, 0)} / ${items.reduce((total, item) => total + item.max_points, 0)} 分`;
  $("score-items").replaceChildren();
  for (const dimension of report.dimensions) {
    const group = items.filter((item) => item.dimension === dimension.label);
    const details = node("details", undefined, "score-group");
    details.append(node("summary", `${dimension.label} · ${dimension.points ?? dimension.score} / ${dimension.max_points ?? 100} 分`));
    for (const item of group) details.append(criterion(item));
    $("score-items").append(details);
  }
  $("score-version").textContent = `规则 ${report.meta.scoring_version} · 评分快照 ${report.meta.score_snapshot_id || "—"} · ${report.meta.score_cache === "hit" ? "已复核相同输入的评分结果" : "已按固定规则计算"}`;
  $("advice-summary").hidden = !report.advice_summary && !report.advice_error;
  $("advice-summary").textContent = report.advice_error ? `规则评分已完成。AI 建议暂不可用：${report.advice_error}` : (report.advice_summary ? `AI 建议：${report.advice_summary}` : "");
  const requirements = report.requirements || [];
  $("requirements-section").hidden = !requirements.length;
  $("requirements").replaceChildren();
  for (const item of requirements) $("requirements").append(criterion(item));
  const market = report.market || { score: null, message: "暂无行情评估", items: [] };
  const application = report.application || { score: null, message: "暂无综合评估", items: [] };
  $("market-score").textContent = market.score === null ? "未评估" : `${market.score} / 100`;
  $("application-score").textContent = application.score === null ? "未评估" : `${application.score} / 100`;
  $("market-message").textContent = market.message;
  $("market-scope").textContent = `${market.target_label || ""}。${market.scope || ""}${market.captured_at ? ` · 采集于 ${new Date(market.captured_at).toLocaleString("zh-CN")}` : ""} · 当前范围 ${market.sample_count || 0} 条职位 / ${market.company_count || 0} 家企业`;
  $("market-method").textContent = [market.method, market.limitations].filter(Boolean).join(" ");
  $("market-items").replaceChildren();
  for (const item of market.items) $("market-items").append(criterion(item));
  $("application-method").textContent = application.message;
  $("application-items").replaceChildren();
  for (const item of application.items) {
    const row = node("p", `${item.title}：${item.points} / ${item.max_points} 分 `, "application-row");
    const button = node("button", "查看来源", "evidence-button");
    button.type = "button";
    button.addEventListener("click", () => {
      const target = $(`criterion-${item.source_id}`);
      if (!target) return;
      let ancestor = target.parentElement;
      while (ancestor) { if (ancestor.tagName === "DETAILS") ancestor.open = true; ancestor = ancestor.parentElement; }
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    row.append(button);
    $("application-items").append(row);
  }
}

function renderReport(report) {
  $("report-source").textContent = report.source === "ai" ? "规则评分 · AI 建议" : "固定规则评分";
  $("overall-score").textContent = report.overall_score;
  $("score-ring").style.setProperty("--score", report.overall_score);
  $("match-score").textContent = report.job_match_score === null ? "—" : `${report.job_match_score} / 100`;
  $("match-kind").textContent = !report.meta.job_provided ? "未提供岗位描述" : (report.job_match_score === null ? "暂未识别到可评估要求" : "按必备、加分与替代关系计算");
  $("report-summary").textContent = report.summary;
  $("profile-row").replaceChildren();
  for (const [key, label] of [["name", "姓名"], ["education", "学历"], ["email", "邮箱"], ["phone", "电话"]]) {
    const item = node("span", label);
    item.append(node("strong", report.profile[key]));
    $("profile-row").append(item);
  }
  $("dimensions").replaceChildren();
  for (const dimension of report.dimensions) {
    const item = node("div", undefined, "dimension");
    const top = node("div", undefined, "dimension-top");
    top.append(node("span", dimension.label), node("strong", `${dimension.score} / 100`));
    const track = node("div", undefined, "dimension-track");
    track.setAttribute("role", "meter");
    track.setAttribute("aria-label", dimension.label);
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(dimension.score));
    const fill = node("div", undefined, "dimension-fill");
    fill.style.width = `${dimension.score}%`;
    track.append(fill);
    item.append(top, track, node("p", dimension.detail));
    $("dimensions").append(item);
  }
  renderChips("skills", report.skills, "", "暂未识别到技能，建议在简历中清晰列出专业技能。");
  $("job-skills").hidden = !report.meta.job_provided;
  renderChips("matched-skills", report.matched_skills, "matched", "当前未检索到共同的技能关键词。");
  renderChips("missing-skills", report.missing_skills, "missing", report.matched_skills.length ? "已识别到的岗位关键词均已覆盖。" : "当前没有可展示的缺失关键词。");
  $("strengths").replaceChildren();
  for (const strength of report.strengths) {
    const item = node("li");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-check");
    icon.append(use);
    item.append(icon, node("span", strength));
    $("strengths").append(item);
  }
  $("suggestions").replaceChildren();
  for (const suggestion of report.suggestions) {
    const item = node("div", undefined, "suggestion");
    const detail = node("div");
    detail.append(node("h4", suggestion.title), node("p", suggestion.detail));
    item.append(node("span", `${suggestion.priority}优先`, `priority ${suggestion.priority === "高" ? "high" : ""}`), detail);
    $("suggestions").append(item);
  }
  $("rewrites-section").hidden = !report.rewrites.length;
  $("rewrites").replaceChildren();
  for (const rewrite of report.rewrites) {
    const item = node("div", undefined, "rewrite");
    item.append(node("div", "原始描述", "rewrite-label"), node("p", rewrite.before), node("div", "改写参考", "rewrite-label after-label"), node("p", rewrite.after, "after"));
    $("rewrites").append(item);
  }
  $("interview-questions").replaceChildren();
  for (const question of report.interview_questions) $("interview-questions").append(node("li", question));
  const analyzedAt = new Date(report.meta.analyzed_at).toLocaleString("zh-CN");
  $("report-footnote").textContent = `生成于 ${analyzedAt} · ${report.meta.characters.toLocaleString()} 字符`;
  renderScoring(report);
  $("empty-state").hidden = true;
  $("report").hidden = false;
}

async function readAnalysis(response, onActivity) {
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { throw new Error(`分析服务返回异常（${response.status}），请稍后重试。`); }
    throw new Error(typeof data?.detail === "string" ? data.detail : "输入内容不正确，请检查后重试。");
  }
  if (!response.headers.get("content-type")?.toLowerCase().includes("text/event-stream")) return response.json();
  if (!response.body || typeof response.body.getReader !== "function") throw new Error("浏览器暂不支持实时读取，请刷新后重试。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const processFrame = (frame) => {
    const payload = frame.split(/\r\n|\r|\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, "")).join("\n");
    if (!payload) return null;
    const event = JSON.parse(payload);
    if (event.type === "error") throw new Error(event.message);
    if (event.type === "status") $("loading-description").textContent = event.message;
    if (event.type === "advice_error") $("loading-description").textContent = "规则评分已完成，正在保留评分结果……";
    if (event.type === "summary") {
      $("stream-preview").hidden = false;
      $("stream-summary").textContent = event.text;
      $("loading-description").textContent = "正在整理详细评估与优化建议……";
    }
    return event.type === "complete" ? event.report : null;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (!done) onActivity();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer)) !== null) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const report = processFrame(frame);
        if (report) return report;
      }
      if (done) {
        const report = processFrame(buffer);
        if (report) return report;
        break;
      }
    }
    throw new Error("分析连接已中断，请稍后重试。");
  } finally {
    try { await reader.cancel(); } finally { reader.releaseLock(); }
  }
}

$("analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  $("error-message").hidden = true;
  const form = new FormData();
  if (inputMode === "upload") {
    const file = droppedFile || $("resume-file").files[0];
    if (!file) { toast("请先上传简历，或切换到粘贴文本"); return; }
    if (!/\.(pdf|docx|txt)$/i.test(file.name)) { toast("请选择 PDF、DOCX 或 TXT 文件"); return; }
    if (file.size > 8 * 1024 * 1024) { toast("文件不能超过 8 MB"); return; }
    form.append("file", file);
  } else {
    const text = $("resume-text").value.trim();
    if (text.length < 60) { toast("请至少输入 60 字符的简历正文"); $("resume-text").focus(); return; }
    form.append("resume_text", text);
  }
  form.append("job_description", $("job-description").value);
  for (const key of ["industry", "role", "level", "city"]) form.append(key, $(`target-${key}`).value.trim());
  form.append("use_market", String($("use-market").checked));
  form.append("use_ai", String($("use-ai").checked));
  const useAI = $("use-ai").checked;
  form.append("stream", String(useAI && typeof ReadableStream !== "undefined" && typeof TextDecoder !== "undefined"));
  $("stream-preview").hidden = true;
  $("stream-summary").textContent = "";
  $("loading-description").textContent = useAI ? "正在评估岗位契合度与项目经历，通常需要 10—60 秒……" : "正在整理技能与改进建议……";
  $("empty-state").hidden = true;
  $("report").hidden = true;
  setBusy(true);
  const controller = new AbortController();
  let timer;
  const onActivity = () => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), useAI ? 120000 : 30000);
  };
  onActivity();
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form, signal: controller.signal });
    const data = await readAnalysis(response, onActivity);
    currentReport = data;
    renderReport(data);
    toast("分析完成，报告已生成");
    if (window.innerWidth < 930) $("report").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("error-message").textContent = error.name === "AbortError" ? "分析超时，请稍后重试。" : (error instanceof TypeError ? "暂时无法连接服务，请稍后重试。" : error.message);
    $("error-message").hidden = false;
    if (currentReport) $("report").hidden = false;
    else $("empty-state").hidden = false;
  } finally { clearTimeout(timer); setBusy(false); }
});

function markdown(report) {
  const lines = ["# 简历分析报告", "", `分析方式：${report.source === "ai" ? "AI 深度分析" : "基础分析"}`,
    `简历参考分：${report.overall_score}/100`, `规则版本：${report.meta.scoring_version}；评分快照：${report.meta.score_snapshot_id || "—"}`, "分数由固定规则计算，AI 仅提供建议。", `岗位要求匹配：${report.job_match_score === null ? "未评估" : report.job_match_score + "/100"}`, "", report.summary,
    "", "## 基本信息", ...Object.entries(report.profile).map(([key, value]) => `- ${{name:"姓名",education:"学历",email:"邮箱",phone:"电话"}[key]}：${value}`),
    "", "## 多维评估", ...report.dimensions.map((v) => `- ${v.label}：${v.score}/100。${v.detail}`),
    "", "## 技能关键词", `已识别：${report.skills.join("、") || "无"}`, `岗位已覆盖：${report.matched_skills.join("、") || "无"}`, `岗位未检索到：${report.missing_skills.join("、") || "无"}`,
    "请结合岗位要求和真实经历核对，技能匹配不代表实际熟练程度。", "", "## 优势", ...report.strengths.map((v) => `- ${v}`), "", "## 改进建议",
    ...report.suggestions.map((v) => `- [${v.priority}优先] ${v.title}：${v.detail}`), "", "## 改写参考"];
  for (const rewrite of report.rewrites) lines.push("", `原句：${rewrite.before}`, "", `建议：${rewrite.after}`);
  if (report.advice_summary) lines.push("", "## AI 建议", report.advice_summary);
  if (report.advice_error) lines.push("", "AI 建议暂不可用：" + report.advice_error + "；规则评分已完成。");
  const appendItems = (title, items) => {
    lines.push("", `## ${title}`);
    for (const item of items || []) {
      lines.push("", `### ${item.title}：${item.points}/${item.max_points} 分`, `规则：${item.rule}`);
      if (item.observation) lines.push(item.observation);
      if (item.missing) lines.push(`待补充：${item.missing}`);
      for (const reference of [...(item.evidence || []), ...(item.resume_evidence || [])]) lines.push(`依据（${reference.source === "job" ? "岗位" : "简历"}，字符 ${reference.start}—${reference.end}）：`, `> ${reference.quote.replace(/\n/g, "\n> ")}`);
      for (const source of item.sources || []) lines.push(`- ${source.company} · ${source.title}：${source.url}`, `  > ${source.quote}`);
    }
  };
  appendItems("逐项评分依据", report.score_items);
  appendItems("岗位要求", report.requirements);
  if (report.market) {
    lines.push("", "## 行情评估", `行情要求适配：${report.market.score === null ? "未评估" : report.market.score + "/100"}`, report.market.message, report.market.scope,
      `行情快照：${report.market.snapshot_id || "—"}；采集时间：${report.market.captured_at || "—"}`, report.market.limitations);
    appendItems("行情能力与职位来源", report.market.items);
  }
  if (report.application) {
    lines.push("", "## 投递综合参考", `分数：${report.application.score === null ? "未评估" : report.application.score + "/100"}`, report.application.message);
    for (const item of report.application.items) lines.push(`- ${item.title}：${item.points}/${item.max_points} 分，来源条目 ${item.source_id}`);
  }
  lines.push("", "方括号内容请替换为真实经历和数据。", "", "## 面试准备", ...report.interview_questions.map((v, i) => `${i + 1}. ${v}`), "", `生成时间：${report.meta.analyzed_at}`);
  return lines.join("\n");
}

function downloadReport() {
  if (!currentReport) return;
  const blob = new Blob([markdown(currentReport)], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = "简历分析报告.md";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-md").addEventListener("click", downloadReport);

fetch("/api/status").then((response) => {
  if (!response.ok) throw new Error("服务不可用");
  return response.json();
}).then((status) => {
  aiAvailable = status.ai_available;
  $("service-status").lastChild.textContent = "可以开始分析";
  $("analysis-mode").hidden = !aiAvailable;
  $("use-ai").disabled = busy || !aiAvailable;
}).catch(() => {
  $("service-status").lastChild.textContent = "服务连接失败";
});

let marketRequest = 0;
let marketTimer;

function renderMarketCoverage(market) {
  $("market-context-status").textContent = market.available
    ? `快照共 ${market.jobs} 条职位 · ${market.companies} 家企业${market.captured_at ? ` · ${new Date(market.captured_at).toLocaleDateString("zh-CN")} 更新` : ""}`
    : "暂无有效行情样本，仍可进行简历与岗位分析。";
  const selection = market.selection;
  const enabled = $("use-market").checked;
  $("market-selection-status").classList.toggle("ready", enabled && selection?.status === "available");
  $("market-selection-status").textContent = !enabled ? "本次未启用行情评估。" : selection
    ? selection.status === "available"
      ? `当前范围 ${selection.sample_count} 条职位 / ${selection.company_count} 家企业，已达到评估门槛。`
      : selection.status === "needs_context"
        ? `当前范围 ${selection.sample_count} 条职位 / ${selection.company_count} 家企业。${selection.message}`
        : selection.message
    : "选择行业、岗位族和职业阶段后，查看实际可用样本。";
  $("market-city-options").replaceChildren();
  if (!enabled || !market.available) return;
  const regions = [...(market.regions || [])].sort((a, b) => (b.city === "中国") - (a.city === "中国") || b.jobs - a.jobs).slice(0, 7);
  for (const region of [...regions, { city: "", jobs: market.jobs }]) {
    const button = node("button", region.city ? `${region.city} · ${region.jobs}` : "不限地区");
    button.type = "button";
    button.title = region.city ? `${region.city}：${region.jobs} 条职位 / ${region.companies} 家企业` : "使用当前快照中所有地区的样本";
    button.setAttribute("aria-pressed", String($("target-city").value.trim().replace(/市$/, "") === region.city));
    button.addEventListener("click", () => {
      $("target-city").value = region.city;
      refreshMarketCoverage();
    });
    $("market-city-options").append(button);
  }
}

async function refreshMarketCoverage() {
  clearTimeout(marketTimer);
  const request = ++marketRequest;
  const query = new URLSearchParams();
  for (const key of ["industry", "role", "level", "city"]) query.set(key, $(`target-${key}`).value.trim());
  try {
    const response = await fetch(`/api/market?${query}`);
    if (!response.ok) throw new Error("market unavailable");
    const market = await response.json();
    if (request === marketRequest) renderMarketCoverage(market);
  } catch {
    if (request !== marketRequest) return;
    $("market-context-status").textContent = "行情快照信息暂不可用";
    $("market-selection-status").textContent = "暂时无法读取行情覆盖范围，仍可进行简历分析。";
    $("market-selection-status").classList.remove("ready");
    $("market-city-options").replaceChildren();
  }
}

for (const key of ["industry", "role", "level"]) $("target-" + key).addEventListener("change", refreshMarketCoverage);
$("use-market").addEventListener("change", refreshMarketCoverage);
$("target-city").addEventListener("input", () => {
  clearTimeout(marketTimer);
  marketRequest++;
  $("market-selection-status").classList.remove("ready");
  $("market-selection-status").textContent = "正在查询该地区样本……";
  marketTimer = setTimeout(refreshMarketCoverage, 250);
});
refreshMarketCoverage();
