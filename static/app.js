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
  const file = $("resume-file").files[0];
  $("file-label").textContent = file ? file.name : "点击上传，或将文件拖到这里";
  $("file-hint").textContent = file ? "文件已选择，准备好后开始分析" : "支持 PDF、DOCX、TXT · 最大 8 MB";
  $("file-actions").hidden = !file;
  $("file-size").textContent = file ? `${(file.size / 1024).toFixed(1)} KB` : "";
}
$("resume-file").addEventListener("change", updateFile);
$("clear-file").addEventListener("click", () => { $("resume-file").value = ""; updateFile(); });
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
  const transfer = new DataTransfer();
  transfer.items.add(event.dataTransfer.files[0]);
  $("resume-file").files = transfer.files;
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
$("guide-button").addEventListener("click", () => $("guide-dialog").showModal());
$("guide-dialog").addEventListener("click", (event) => {
  if (event.target === $("guide-dialog")) {
    const bounds = $("guide-dialog").getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $("guide-dialog").close();
  }
});
$("use-ai").addEventListener("change", () => { $("ai-privacy").hidden = !$("use-ai").checked; });

function setBusy(value) {
  busy = value;
  for (const element of $("analysis-form").querySelectorAll("input, textarea, button")) element.disabled = value;
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

function renderReport(report) {
  $("report-source").textContent = report.source === "ai" ? "AI 深度分析" : "基础分析";
  $("overall-score").textContent = report.overall_score;
  $("score-ring").style.setProperty("--score", report.overall_score);
  $("match-score").textContent = report.job_match_score === null ? "—" : `${report.job_match_score}%`;
  $("match-kind").textContent = !report.meta.job_provided ? "未提供岗位描述" : (
    report.source === "ai" ? (report.job_match_score === null ? "暂未评估" : "综合评估") : (report.job_match_score === null ? "暂未识别到岗位技能" : "关键词覆盖率"));
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
  $("empty-state").hidden = true;
  $("report").hidden = false;
}

async function readAnalysis(response, onActivity) {
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === "string" ? data.detail : "输入内容不正确，请检查后重试。");
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream")) return response.json();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      onActivity();
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const payload = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!payload) continue;
        const event = JSON.parse(payload);
        if (event.type === "error") throw new Error(event.message);
        if (event.type === "status") $("loading-description").textContent = event.message;
        if (event.type === "summary") {
          $("stream-preview").hidden = false;
          $("stream-summary").textContent = event.text;
          $("loading-description").textContent = "正在整理详细评估与优化建议……";
        }
        if (event.type === "complete") return event.report;
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
    const file = $("resume-file").files[0];
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
  form.append("use_ai", String($("use-ai").checked));
  const useAI = $("use-ai").checked;
  form.append("stream", String(useAI));
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
    `简历参考分：${report.overall_score}/100`, `岗位匹配：${report.job_match_score === null ? "未评估" : report.job_match_score + "%"}（${report.source === "ai" ? "AI 综合评估" : "关键词覆盖率"}）`, "", report.summary,
    "", "## 基本信息", ...Object.entries(report.profile).map(([key, value]) => `- ${{name:"姓名",education:"学历",email:"邮箱",phone:"电话"}[key]}：${value}`),
    "", "## 多维评估", ...report.dimensions.map((v) => `- ${v.label}：${v.score}/100。${v.detail}`),
    "", "## 技能关键词", `已识别：${report.skills.join("、") || "无"}`, `岗位已覆盖：${report.matched_skills.join("、") || "无"}`, `岗位未检索到：${report.missing_skills.join("、") || "无"}`,
    "请结合岗位要求和真实经历核对，技能匹配不代表实际熟练程度。", "", "## 优势", ...report.strengths.map((v) => `- ${v}`), "", "## 改进建议",
    ...report.suggestions.map((v) => `- [${v.priority}优先] ${v.title}：${v.detail}`), "", "## 改写参考"];
  for (const rewrite of report.rewrites) lines.push("", `原句：${rewrite.before}`, "", `建议：${rewrite.after}`);
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
