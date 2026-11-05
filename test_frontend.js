"use strict";

// node --test test_frontend.js；使用 Node 内置模块验证浏览器通信，无需安装前端依赖。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function browser() {
  const elements = new Map();
  const makeElement = (tag) => ({
    tagName: tag.toUpperCase(), listeners: {}, files: [], value: "", hidden: false, children: [],
    lastChild: { textContent: "" }, classList: { add() {}, remove() {}, toggle() {} },
    style: { setProperty() {} },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    querySelector(selector) { return element(`${this.id || tag} ${selector}`); },
    querySelectorAll() { return []; },
    replaceChildren(...values) { this.children = []; this.append(...values); },
    append(...values) { for (const value of values) { value.parentElement = this; this.children.push(value); } },
  });
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { ...makeElement("div"), id });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element, createElement: makeElement },
    window: { innerWidth: 1200 }, Response, ReadableStream, TextDecoder, FormData, AbortController, URL, URLSearchParams,
    setTimeout() { return 0; }, clearTimeout() {},
    fetch: async () => new Response(JSON.stringify({ ai_available: false })),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "static/app.js"), "utf8"), context);
  return { context, element };
}

function sseResponse(text, chunkSize = 3) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      for (let index = 0; index < bytes.length; index += chunkSize) controller.enqueue(bytes.slice(index, index + chunkSize));
      controller.close();
    },
  }), { headers: { "content-type": "text/event-stream" } });
}

test("SSE handles split UTF-8, CRLF, comments and a final frame without a separator", async () => {
  const { context, element } = browser();
  const body = ": heartbeat\r\n\r\n"
    + 'data: {"type":"summary","text":"简历评价：有具体实践。"}\r\n\r\n'
    + 'data: {"type":"complete","report":{"overall_score":72}}';
  let activity = 0;
  const report = await context.readAnalysis(sseResponse(body, 1), () => { activity++; });
  assert.equal(report.overall_score, 72);
  assert.equal(element("stream-summary").textContent, "简历评价：有具体实践。");
  assert.equal(element("stream-preview").hidden, false);
  assert.ok(activity > 10);
});

test("SSE supports LF and CR line endings", async () => {
  for (const newline of ["\n", "\r"]) {
    const { context } = browser();
    const response = sseResponse('data: {"type":"status","message":"分析中"}' + newline + newline
      + 'data: {"type":"complete","report":{"overall_score":80}}' + newline + newline);
    assert.equal((await context.readAnalysis(response, () => {})).overall_score, 80);
  }
});

test("an interrupted SSE stream never returns a successful report", async () => {
  const { context } = browser();
  await assert.rejects(context.readAnalysis(sseResponse('data: {"type":"summary","text":"尚未完成"}\n\n'), () => {}), /中断/);
  await assert.rejects(context.readAnalysis(sseResponse('data: {"type":"error","message":"服务繁忙"}\n\n'), () => {}), /服务繁忙/);
});

test("JSON reports and proxy HTML errors are handled", async () => {
  const { context } = browser();
  assert.equal((await context.readAnalysis(new Response('{"overall_score":77}'), () => {})).overall_score, 77);
  await assert.rejects(context.readAnalysis(new Response("<html>Bad gateway</html>", { status: 502 }), () => {}), /502/);
});

test("dragged files work without DataTransfer and unsupported streaming falls back to JSON", async () => {
  const { context, element } = browser();
  const file = new File(["example resume"], "resume.txt", { type: "text/plain" });
  element("drop-zone").listeners.drop({ dataTransfer: { files: [file] } });
  assert.equal(element("file-label").textContent, "resume.txt");
  context.ReadableStream = undefined;
  context.TextDecoder = undefined;
  context.renderReport = () => {};
  element("use-ai").checked = true;
  let form;
  context.fetch = async (_url, options) => {
    form = options.body;
    return new Response('{"overall_score":72}');
  };
  await element("analysis-form").listeners.submit({ preventDefault() {} });
  assert.equal(form.get("stream"), "false");
  assert.equal(form.get("file").name, "resume.txt");
  element("clear-file").listeners.click();
  assert.equal(element("file-actions").hidden, true);
});

test("guide opens and closes when native dialog methods are unavailable", () => {
  const { element } = browser();
  element("guide-button").listeners.click();
  assert.equal(element("guide-dialog").open, "");
  element("guide-dialog form").listeners.submit({ preventDefault() {} });
  assert.equal(element("guide-dialog").open, undefined);
});

test("AI advice failure preserves a complete rule report", async () => {
  const { context } = browser();
  const response = sseResponse('data: {"type":"advice_error","message":"AI建议超时"}\n\n'
    + 'data: {"type":"complete","report":{"overall_score":76,"source":"local","advice_status":"failed"}}\n\n');
  const report = await context.readAnalysis(response, () => {});
  assert.equal(report.overall_score, 76);
  assert.equal(report.source, "local");
  assert.equal(report.advice_status, "failed");
});

test("evidence viewer uses Unicode code point offsets and a safe text highlight", () => {
  const { context, element } = browser();
  vm.runInContext('currentReport = {source_text: {resume: "😀\\nPython 使用记录"}}', context);
  context.openEvidence({ source: "resume", start: 2, end: 8, quote: "Python" });
  assert.equal(element("evidence-location").textContent, "简历正文 · 第 2 行");
  assert.equal(element("evidence-text").children[1].tagName, "MARK");
  assert.equal(element("evidence-text").children[1].textContent, "Python");
  assert.equal(element("evidence-dialog").open, "");
});

test("rule ledger, missing market data and advice status are rendered explicitly", () => {
  const { context, element } = browser();
  context.renderScoring({
    meta: { scoring_version: "3.0.0", score_snapshot_id: "example", score_cache: "hit" },
    dimensions: [{ label: "成果表达", points: 3, max_points: 5 }],
    score_items: [{ id: "results.example", dimension: "成果表达", title: "验证条件", points: 3, max_points: 5, rule: "固定条件", evidence: [] }],
    requirements: [], advice_error: "建议超时", market: { score: null, message: "上海样本不足", items: [], sample_count: 0, company_count: 0 },
    application: { score: null, message: "未评估", items: [] },
  });
  assert.equal(element("score-ledger-total").textContent, "3 / 5 分");
  assert.equal(element("score-items").children.length, 1);
  assert.equal(element("market-score").textContent, "未评估");
  assert.equal(element("advice-summary").hidden, false);
  assert.match(element("advice-summary").textContent, /规则评分已完成/);
  assert.match(element("score-version").textContent, /example/);
});

test("market coverage keeps a missing city until the user chooses another region", async () => {
  const { context, element } = browser();
  element("use-market").checked = true;
  element("target-city").value = "深圳";
  const coverage = { available: true, jobs: 300, companies: 4,
    selection: { status: "insufficient", message: "深圳只有 12 条职位、2 家企业，暂不计分。" },
    regions: [{ city: "中国", jobs: 150, companies: 3 }, { city: "北京", jobs: 60, companies: 3 }],
  };
  context.renderMarketCoverage(coverage);
  assert.equal(element("target-city").value, "深圳");
  assert.match(element("market-selection-status").textContent, /深圳.*暂不计分/);
  let requested;
  context.fetch = async (url) => { requested = new URL(url, "http://localhost"); return new Response(JSON.stringify(coverage)); };
  element("market-city-options").children.find((button) => button.textContent.startsWith("北京")).listeners.click();
  assert.equal(element("target-city").value, "北京");
  assert.equal(requested.searchParams.get("city"), "北京");
});

test("older coverage responses cannot overwrite the latest city selection", async () => {
  const { context, element } = browser();
  element("use-market").checked = true;
  const pending = [];
  context.fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }));
  element("target-city").value = "北京";
  const oldRequest = context.refreshMarketCoverage();
  element("target-city").value = "上海";
  const latestRequest = context.refreshMarketCoverage();
  const response = (city) => new Response(JSON.stringify({ available: true, jobs: 80, companies: 3, regions: [],
    selection: { status: "insufficient", message: city + "样本不足" } }));
  pending[1].resolve(response("上海"));
  await latestRequest;
  pending[0].resolve(response("北京"));
  await oldRequest;
  assert.equal(element("market-selection-status").textContent, "上海样本不足");
  assert.equal(element("target-city").value, "上海");
});
