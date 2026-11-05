# 简历透镜 · AI 简历分析系统

Python 3.10+、FastAPI 与原生 HTML/CSS/JavaScript。支持 PDF、DOCX、TXT 上传或粘贴正文，由AI依照严格细则逐项评分，提供原文依据、岗位要求匹配、招聘行情参考和修改建议。

## 启动

```bash
git clone https://github.com/ChaceQC/ai-resume-analyzer.git
cd ai-resume-analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 在 .env 填写 AI_API_KEY、AI_BASE_URL、AI_MODEL 后启动
python app.py
```

打开 [http://127.0.0.1:5000](http://127.0.0.1:5000)，点击“用示例体验”，再开始分析。接口文档在 `/docs`。Windows 可使用 `py -m venv .venv`、`.venv\Scripts\activate`，然后执行安装和启动命令。已安装 uv 时，也可以使用 `uv venv`、`uv pip install --python .venv/bin/python -r requirements.txt`。

默认监听本机；通过 `.env` 的 `HOST`、`PORT` 修改监听地址。使用 uvicorn 命令启动时，需要显式指定 `--host`、`--port`。

## AI评分与逐项依据

4.0 起，AI读取完整简历和岗位描述，依照评分细则进行语义判断。服务端不预先计算基础分，也不把旧分数传给模型；服务端只校验项目编号、得分范围和引用，并按公开权重加总。完整细则及评审原则见 `rubric.py` 和 `/api/rubric`。

| 质量维度 | 满分 | 评价依据 |
| --- | ---: | --- |
| 信息完整度 | 5 | 联系方式、姓名、教育或资格背景 |
| 内容结构 | 10 | 经历背景、任务、行动和时间是否清楚 |
| 技能呈现 | 25 | 实际应用、方法与方案取舍、验证与反馈 |
| 成果表达 | 45 | 个人贡献、交付价值、结果口径、可比条件及可信度 |
| 表达清晰度 | 15 | 信息密度、重复套话、具体程度和上下文一致性 |

20 个质量条目由AI分别给出整数得分、理由和原文引用，加总为100分。满分和权重由细则规定；模型不能改写维度、权重或直接指定总分。大于0分的项目必须有能在简历中定位的引用；缺项、重复编号、越界分数、虚构引用会使本次评分失败。

技能清单、泛泛职责和漂亮数字不能替代实际贡献。80分以上需要较充分的个人贡献和验证依据，85分以上需要多项能关联职责、方法、验证条件与结果的经历，95分以上应极少见。学校名气、性别、年龄等不作为评分依据。模型按校招、初中级、资深或管理阶段判断复杂度，不核实业绩真伪，也不推断录用概率。

岗位要求由AI从完整岗位描述提取，包含学历、资格、年限等非关键词条件。满足、部分满足、没有证据分别折算为该项100%、50%、0%，必备和加分权重分别为3和1；可替代条件应合并，不重复计数。程序验证岗位引用和简历引用，判断本身由AI完成。

请求默认使用temperature=0，但不同运行或模型版本仍可能有差异，不承诺相同输入每次同分。SQLite只保存指纹、分值、细则版本和时间；指纹包含模型、输入、行情版本和本次分值。不同模型判断形成不同记录，旧记录不会覆盖新判断；数据库不缓存或替代模型评分。

## 行情数据与适用范围

随项目提供 2026-11-05 采集的企业公开招聘试点快照，新增腾讯、网易、美团官网的国内岗位，并保留国际企业样本。快照覆盖软件互联网、金融科技、医疗健康、制造工程；新增国内来源主要属于软件互联网行业。样本清单、来源与覆盖分组见 `market_data/snapshot.manifest.json`；完整样本在 `market_data/snapshot.json.gz`，可通过网页或 `/api/market/snapshot` 下载 JSON。

分析时按行业、岗位族、职业阶段和指定地区严格筛选。网页默认中国地区；修改筛选条件后，会提前显示当前范围的职位数、企业数和是否达到评估门槛，并提供常见城市的样本数量与切换按钮。地区留空表示快照内不限地区；城市无样本时不会悄悄扩大范围。岗位族可从岗位标题识别，也可手动选择；行情需要行业、岗位族和职级都明确。职业阶段优先使用来源明确提供的实习、校招类型，其余按标题推断；“产品经理”“项目经理”等职务名称不会仅因含“经理”就归为团队管理岗。

试点规则：

- 保留企业招聘接口当前列出的职位，筛选最近 60 天更新的记录；更新时间不等于首次发布时间。
- 按公司、职位、地点和要求去重；每家企业的同岗位族、同职级、同工作地点最多取 25 条，避免不同城市相互挤占抽样名额。一个职位可列出多个工作地点，地区数量不可直接相加。
- 单次行情评估至少需要 30 条样本、3 家企业。这是试点覆盖门槛，不是统计代表性的保证。
- 取至少两家企业共同提及的能力，按提及企业数而非重复职位数分配权重，最多展示 10 项。
- 对每项能力：AI判断是否有具体应用、仅正向陈述或无证据，分别折算为该项100%、25%、0%；保存支持该要求的职位原句、链接、公司、地点和更新时间。
- 行情快照超过 14 天、缺失、校验失败或样本不足时，行情分和投递综合分显示“未评估”；简历质量与岗位匹配仍正常计算。

投递综合分只在岗位与行情都可评估时计算：质量 40 分、岗位 50 分、行情补充 10 分。行情补充只使用目标岗位未明确列出的能力，避免同一项技能在岗位与行情中重复加权；全部行情能力已被岗位要求覆盖时，采用质量 40 分、岗位 60 分。每个综合分条目指向原始评分条目，保留两位小数，最后统一四舍五入。

公开职位样本反映这些企业的招聘要求，不能据此推断求职者供给、整个行业招聘增速、实际成交薪资或个人录用概率。原文片段按采集快照保存，职位页面后续可能变化。

### 更新与导入

分析请求不会自动联网抓取行情。需要刷新时，在项目目录运行：

```bash
.venv/bin/python update_market.py
# 需要本地 HTTP 代理时：
.venv/bin/python update_market.py --proxy http://127.0.0.1:7897
```

更新工具从腾讯、网易、美团的官网公开招聘接口及国际企业的 Greenhouse 公开接口获取数据，读取分页与必要的职位详情，生成校验指纹、压缩快照和可读清单；没有有效样本时保留原快照。`--sources` 支持 `all`（默认）、`domestic`、`international`；通过 `--output` 指定输出路径，通过 `--cache-dir` 复用 24 小时内的原始接口响应。原始响应不需要加入仓库。

也支持从公开或已授权的数据源导入标准 JSON：

```json
{
  "captured_at": "2026-11-05T02:00:00+00:00",
  "sources": [{"company": "示例企业", "url": "https://example.org/careers"}],
  "entries": [{
    "id": "job-001", "company": "示例企业", "industry": "software",
    "title": "Software Engineer", "location": "Shanghai, China",
    "url": "https://example.org/careers/job-001",
    "updated_at": "2026-11-05T01:00:00+00:00",
    "content": "<h2>Requirements</h2><p>Experience with Python and SQL.</p>"
  }]
}
```

```bash
.venv/bin/python update_market.py --input imported-jobs.json
```

上面的示例用于说明格式，不是实际行情。行业代码包括 `software`、`finance`、`healthcare`、`engineering`；岗位和职级选项见 `/api/market`。导入来源需要有准确的采集时间、职位链接、更新时间与原文。

## 配置AI服务

评分需要配置AI服务。行情覆盖范围和样本下载不需要密钥。首次配置可复制 `.env.example`，已有 `.env` 时直接编辑：

```dotenv
AI_API_KEY=服务商密钥
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
AI_PROXY=
HOST=127.0.0.1
PORT=5000
```

基础 URL 支持域名、自定义路径或完整 `/chat/completions` 地址，保留查询参数。服务需要兼容 Chat Completions。后端支持 SSE、非流式 JSON、文本块和带 Markdown 围栏的 JSON；明确报告参数不支持时，可改用 `max_completion_tokens`、移除 `temperature` 或关闭上游流式请求。鉴权、限流和服务错误不会触发这些参数重试。

开始分析会将简历全文、岗位、评分细则及必要的行情要求片段发送至已配置的AI服务，密钥只保留在后端。未配置时返回503；模型超时、断流、截断或评分校验失败时明确报错，不返回固定规则分数或不完整评分。无法核实原句的可选改写会单独省略。

AI 上游读取超时为 90 秒；网页收到活动会重置等待时间。浏览器缺少流式能力时改用 JSON。Nginx 代理应关闭该接口的 `proxy_buffering`，并设置足够长的读取超时。

配置修改后需要重新启动后端。

## 接口

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/api/status` | GET | 服务、版本及 AI 配置状态 |
| `/api/sample` | GET | 示例简历与岗位 |
| `/api/rubric?level=junior` | GET | 固定评分条目和版本 |
| `/api/market` | GET | 行情覆盖范围、地区样本数和评估选项；接受 industry/role/level/city 筛选 |
| `/api/market/snapshot` | GET | 下载可追溯的公开招聘样本 |
| `/api/analyze` | POST | 文件或正文分析；可返回 JSON 或 SSE |

```bash
curl -X POST http://127.0.0.1:5000/api/analyze \
  -F 'file=@samples/resume.txt' \
  -F 'job_description=<samples/job.txt' \
  -F 'industry=software' -F 'role=software' -F 'level=junior' \
  -F 'use_market=true' -F 'use_ai=true'
```

可以使用 `resume_text` 替代文件；同时提供时文件优先。新增可选字段为 `industry`、`role`、`level`、`city`、`use_market`。`use_ai` 默认开启，显式传入false会返回400；`stream`默认关闭。

`stream=true`返回SSE，事件包括`status`、`summary`、`complete`和`error`。只有`complete.report`是完整且通过校验的报告；`error`、连接中断或缺少`complete`均表示失败，不包含降级分数。JSON模式的模型失败返回502；输入无效和服务未配置仍返回相应HTTP错误。

报告新增字段包括 `score_items`、`requirements`、`context`、`market`、`application`、`source_text`。`source_text` 只随本次响应返回以定位原文。引用的 `start/end` 是 Python Unicode 码点的左闭右开坐标，浏览器按码点定位，兼容 emoji 和全角文本。`source=ai`和`meta.scoring_source=ai`表示逐项判断来自模型，`observation`是AI的得分理由。

## 文件、数据与隐私

文件上限 8 MB，PDF 上限 30 页，简历正文 60—40000 字符，岗位描述上限 12000 字符。PDF 扫描件需先 OCR，旧版 DOC 需另存为 DOCX。

TXT 支持 UTF-8（含 BOM）、GB18030、UTF-16 和带 BOM 的 UTF-32；DOCX 按顺序读取正文、表格与嵌套表格，并提取页眉、页脚、正文文本框。

不保存上传文件、简历正文、岗位正文、引用原句或 AI 建议。默认 `data/scores.sqlite3` 只保存哈希、分值、规则版本与评分时间，`data/` 已加入 Git 忽略。可用 `SCORE_CACHE_PATH` 指定路径；删除数据库后可重新生成。数据库不可写时保留本次AI评估，报告标明存储不可用。`MARKET_SNAPSHOT_PATH` 可指定另一份 JSON 或 JSON.GZ 行情快照；相对路径均基于项目目录。

## 验证

```bash
.venv/bin/python -m unittest -v
node --test test_frontend.js
```

Python测试使用模拟模型，覆盖AI默认评分、模型分值决定结果、禁止规则降级、严格编号/范围/引用校验、岗位要求权重、行情门槛和企业去重、综合分去重、评估记录隔离、文件解析及上游流式接口兼容。自动化测试不调用外部服务，`test_support.py`的合成材料和模拟分值不是真实行情或候选人业绩。

网页测试覆盖SSE、UTF-8分片、代理错误、评分失败、文件拖拽、浏览器降级、Unicode原文定位和地区样本数量。运行应用不需要Node.js。

## 项目结构

```text
app.py                 路由、文件解析与评分流程
rubric.py              AI评分细则、权重和严格评审原则
scoring.py             待评材料准备、文本提取和原文定位
ai_scoring.py          模型逐项分值、引用、岗位及行情判断的校验与加总
score_store.py         不含原文的评分快照存储
market.py              行情筛选、来源与综合分
update_market.py       公开招聘快照更新/导入
market_sources.py      腾讯、网易、美团公开招聘接口及字段映射
analyzer.py            AI评分请求、提示词和流式接口兼容
market_data/           公开行情快照及可读来源清单
static/                页面、逐分明细、原文定位、报告下载
test_app.py            文件、服务与 AI 接口回归
test_scoring.py        AI逐项校验、行情与评估记录回归
test_support.py        模拟模型输出与合成校准材料
test_market_sources.py 国内招聘接口分页、日期和字段映射回归
test_frontend.js       网页通信与报告交互回归
```
