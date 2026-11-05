# 简历透镜 · AI 简历分析系统

Python 3.10+、FastAPI 与原生 HTML/CSS/JavaScript。支持 PDF、DOCX、TXT 上传或粘贴正文，输出固定规则评分、逐项原文依据、岗位要求匹配、招聘行情参考，以及可选的 AI 建议。

## 启动

```bash
git clone https://github.com/ChaceQC/ai-resume-analyzer.git
cd ai-resume-analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

打开 [http://127.0.0.1:5000](http://127.0.0.1:5000)，点击“用示例体验”，再开始分析。接口文档在 `/docs`。Windows 可使用 `py -m venv .venv`、`.venv\Scripts\activate`，然后执行安装和启动命令。已安装 uv 时，也可以使用 `uv venv`、`uv pip install --python .venv/bin/python -r requirements.txt`。

默认监听本机；通过 `.env` 的 `HOST`、`PORT` 修改监听地址。使用 uvicorn 命令启动时，需要显式指定 `--host`、`--port`。

## 评分稳定性与逐分溯源

所有正式分数由固定规则计算，AI 只提供解释、建议、改写和面试问题。AI 返回的评分、维度名称和额外字段均不能覆盖正式评分。

| 质量维度 | 满分 | 评价依据 |
| --- | ---: | --- |
| 信息完整度 | 10 | 姓名、邮箱、电话、教育信息是否提供 |
| 内容结构 | 15 | 教育、能力、实践正文能否定位，经历是否有时间 |
| 技能呈现 | 25 | 正向能力陈述、具体应用、实现方法、验证与反馈 |
| 成果表达 | 35 | 行动、个人职责、交付结果、验证条件、前后对比 |
| 表达清晰度 | 15 | 去空白篇幅、描述重复比例、信息具体程度 |

20 个质量条目直接加总至 100 分。每个条目包含固定编号、满分、实得分、规则版本、原文引用和失分原因。维度栏中的百分制数值只是该维度的进度表示，整体分数使用条目实得分直接相加。完整规则可以在 `/api/rubric` 查看。

支持“项目经历/相关实践/研究经历”等标题别名、段落软换行、全角字符与中英文表述。否定和未来计划不作为已具备的能力；没有个人职责关联的团队成果不直接作为个人成果。日期、电话、版本号和重复内容不会作为新增成果加分。

岗位要求区分必备与加分条件，分别使用权重 3 和 1；“FastAPI 或 Django”按一个可替代要求计算。明确要求实践时，仅有技能陈述获得该条目一半分数。有明确学历、资格或经验年限要求时，按简历中的相应陈述检查；经验年限不从项目数量或教育时间推断。在读状态按岗位是否允许在读判断。未提供证据不等同于确认没有能力。

相同简历、岗位、评估背景、规则和行情快照下，正式评分是确定的，不依赖模型采样或缓存。SQLite 同时保存评分指纹、各项分值及版本，每次分析重算校验；损坏缓存不会覆盖规则结果。重启后相同输入沿用同一评分快照标识与首次评分时间。换岗位、职级、规则或行情版本会生成不同快照。

本次回归包含 20 次绕过缓存的重复计算，以及换行、标题别名、编码、否定、替代条件、重复成果、原文坐标和持久化检查。规则属于初始可解释版本，尚未使用专家标注或真实招聘结果进行统计标定；这些分数不代表录用概率或候选人百分位，也不核实简历业绩真伪。

## 行情数据与适用范围

随项目提供 2026-11-05 采集的企业公开招聘试点快照，新增腾讯、网易、美团官网的国内岗位，并保留国际企业样本。快照覆盖软件互联网、金融科技、医疗健康、制造工程；新增国内来源主要属于软件互联网行业。样本清单、来源与覆盖分组见 `market_data/snapshot.manifest.json`；完整样本在 `market_data/snapshot.json.gz`，可通过网页或 `/api/market/snapshot` 下载 JSON。

分析时按行业、岗位族、职业阶段和指定地区严格筛选。网页默认中国地区；修改筛选条件后，会提前显示当前范围的职位数、企业数和是否达到评估门槛，并提供常见城市的样本数量与切换按钮。地区留空表示快照内不限地区；城市无样本时不会悄悄扩大范围。岗位族可从岗位标题识别，也可手动选择；行情需要行业、岗位族和职级都明确。职业阶段优先使用来源明确提供的实习、校招类型，其余按标题推断；“产品经理”“项目经理”等职务名称不会仅因含“经理”就归为团队管理岗。

试点规则：

- 保留企业招聘接口当前列出的职位，筛选最近 60 天更新的记录；更新时间不等于首次发布时间。
- 按公司、职位、地点和要求去重；每家企业的同岗位族、同职级、同工作地点最多取 25 条，避免不同城市相互挤占抽样名额。一个职位可列出多个工作地点，地区数量不可直接相加。
- 单次行情评估至少需要 30 条样本、3 家企业。这是试点覆盖门槛，不是统计代表性的保证。
- 取至少两家企业共同提及的能力，按提及企业数而非重复职位数分配权重，最多展示 10 项。
- 对每项能力：具体应用得该项 100%，仅正向陈述得 25%，未提供证据得 0%；保存支持该要求的职位原句、链接、公司、地点和更新时间。
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

## AI 建议

基础分析和行情快照评估无需 API Key。首次配置可复制 `.env.example`，已有 `.env` 时直接编辑：

```dotenv
AI_API_KEY=服务商密钥
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
AI_PROXY=
HOST=127.0.0.1
PORT=5000
```

基础 URL 支持域名、自定义路径或完整 `/chat/completions` 地址，保留查询参数。服务需要兼容 Chat Completions。后端支持 SSE、非流式 JSON、文本块和带 Markdown 围栏的 JSON；明确报告参数不支持时，可改用 `max_completion_tokens`、移除 `temperature` 或关闭上游流式请求。鉴权、限流和服务错误不会触发这些参数重试。

AI 只在用户开启开关后调用，简历全文、岗位和评分条目会发送至所配置的服务。密钥只保留在后端。AI 建议失败、截断或断流时，保留已经完成的规则评分，并明确显示建议失败原因；不会把不完整输出当作成功的 AI 建议。无法核实原句的单条改写会被省略。

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
  -F 'use_market=true' -F 'use_ai=false'
```

可以使用 `resume_text` 替代文件；同时提供时文件优先。新增可选字段为 `industry`、`role`、`level`、`city`、`use_market`。`use_ai` 和 `stream` 默认关闭。

`stream=true` 返回 SSE，事件包括 `status`、`summary`、`advice_error`、`complete`。`complete.report` 为完整报告；`advice_status=failed` 表示只有规则报告成功，AI 建议失败。未收到 `complete` 就断开时，不应当作成功。输入无效或未配置却请求 AI 时仍返回 HTTP 错误。

报告新增字段包括 `score_items`、`requirements`、`context`、`market`、`application`、`source_text`、`advice_status`。`source_text` 只随本次响应返回以定位原文。引用的 `start/end` 是 Python Unicode 码点的左闭右开坐标，浏览器按码点定位，兼容 emoji 和全角文本。旧版客户端应根据新字段展示规则来源和建议状态，不能把 `source=ai` 理解成分数由模型计算。

## 文件、数据与隐私

文件上限 8 MB，PDF 上限 30 页，简历正文 60—40000 字符，岗位描述上限 12000 字符。PDF 扫描件需先 OCR，旧版 DOC 需另存为 DOCX。

TXT 支持 UTF-8（含 BOM）、GB18030、UTF-16 和带 BOM 的 UTF-32；DOCX 按顺序读取正文、表格与嵌套表格，并提取页眉、页脚、正文文本框。

不保存上传文件、简历正文、岗位正文、引用原句或 AI 建议。默认 `data/scores.sqlite3` 只保存哈希、分值、规则版本与评分时间，`data/` 已加入 Git 忽略。可用 `SCORE_CACHE_PATH` 指定路径；删除数据库后可重新生成。数据库不可写时继续确定性计算，报告标明存储不可用。`MARKET_SNAPSHOT_PATH` 可指定另一份 JSON 或 JSON.GZ 行情快照；相对路径均基于项目目录。

## 验证

```bash
.venv/bin/python -m unittest -v
node --test test_frontend.js
```

Python 测试覆盖评分区分度、20 次冷算、换行和别名、否定与未来计划、替代条件、必备与加分项、学历年限、团队归属、原文坐标、逐分加总、行情门槛、地区和时效、来源校验、企业去重、综合分去重、SQLite 重启/并发/损坏恢复，以及文件和模型接口回归。自动化测试使用模拟模型和明确标记的测试行情，不调用外部服务。

网页测试使用 Node.js 20+ 内置模块，覆盖 SSE、UTF-8 分片、代理错误、建议失败后保留评分、拖拽、浏览器降级、Unicode 原文定位和未评估状态。运行应用不需要 Node.js。

## 项目结构

```text
app.py                 路由、文件解析与评分流程
scoring.py             确定性条目评分、事实与岗位要求
score_store.py         不含原文的评分快照存储
market.py              行情筛选、来源与综合分
update_market.py       公开招聘快照更新/导入
market_sources.py      腾讯、网易、美团公开招聘接口及字段映射
analyzer.py             可选的 AI 建议和流式接口兼容
market_data/           公开行情快照及可读来源清单
static/                页面、逐分明细、原文定位、报告下载
test_app.py            文件、服务与 AI 接口回归
test_scoring.py        评分、行情、稳定性与存储回归
test_market_sources.py 国内招聘接口分页、日期和字段映射回归
test_frontend.js       网页通信与报告交互回归
```
