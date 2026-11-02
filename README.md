# 简历透镜 · AI 简历分析系统

Python + FastAPI 开发的中文简历分析网页，前端使用原生 HTML / CSS / JavaScript，无需 Node.js 构建或数据库。Python 3.10+。

支持 PDF、DOCX、TXT 上传与文本粘贴、岗位关键词匹配、多维评分、修改建议、项目描述改写、面试问题，以及报告下载。提供一键填充的示例数据；分析接口返回结构化 JSON。

## 快速启动

先获取源码：

```bash
git clone https://github.com/ChaceQC/ai-resume-analyzer.git
cd ai-resume-analyzer
```

然后在项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

如果已安装 uv，也可执行：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python app.py
```

打开 http://127.0.0.1:5000，点击“用示例体验”，再点击“开始分析简历”。接口文档位于 http://127.0.0.1:5000/docs。

PyPI 连接困难时可在安装命令后添加 `--index-url https://mirrors.aliyun.com/pypi/simple`。

## 开启 AI 深度分析

默认使用本地规则，运行时无需 API Key。本地评分基于联系方式、核心模块、内置技能词表、数字化描述和文本长度。岗位匹配分为技能关键词覆盖率；不会把本地规则结果标记为 AI 输出。

首次配置时复制模板；已有 `.env` 时直接编辑，保留自己的配置：

```bash
cp .env.example .env
```

`.env` 已加入 Git 忽略规则，真实密钥不应提交到仓库。

编辑 `.env`，填写支持 Chat Completions 接口的服务，例如：

```dotenv
AI_API_KEY=你的密钥
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
# 不需要代理时留空；使用本机 Clash 普通代理时这样填写。
AI_PROXY=http://127.0.0.1:7897
HOST=127.0.0.1
PORT=5000
```

`AI_BASE_URL` 填到服务商提供的基础路径（例如 `/v1`），不要附加 `/chat/completions`；仅填写域名时，后端会自动补上 `/v1`。`AI_API_KEY` 是该服务商的密钥，`AI_MODEL` 必须是该服务支持的模型名。`AI_PROXY` 仅供后端调用模型时使用，支持 HTTP / HTTPS 代理；不需要时留空。系统代理不会自动传给 Python，使用 Clash 非 TUN 模式时可显式填写本机代理端口。

保存后停止旧后端，重新运行 `.venv/bin/python app.py`，再刷新网页、开启“AI 深度分析”。支持 Chat Completions SSE 流式接口的服务均可使用。配置只保留在后端，前端没有密钥输入或配置说明；AI 开关默认关闭，配置缺失时隐藏。

AI 调用默认启用 `stream: true`，无需额外开关。网页通过 `fetch` 持续读取 SSE，逐步展示总体评价，完整报告生成并校验通过后再显示评分与详细建议。上游网络读取超过 90 秒无响应会超时；网页等待时每次收到数据都会重置超时，不会因为总体耗时较长而中断持续输出。

AI 模式会将简历全文与岗位描述发送至配置的服务；密钥只保留在服务端环境变量中，不返回给浏览器。失败时显示具体错误，可手动切回本地分析。项目不保存上传文件或分析历史。

## 接口示例

```bash
curl -X POST http://127.0.0.1:5000/api/analyze \
  -F 'file=@samples/resume.txt' \
  -F 'job_description=<samples/job.txt' \
  -F 'use_ai=false'
```

也可通过 `resume_text` 字段提交正文。提供文件时优先解析文件。

流式调用（先填写后端 AI 配置）：

```bash
curl -N -X POST http://127.0.0.1:5000/api/analyze \
  -F 'file=@samples/resume.txt' \
  -F 'job_description=<samples/job.txt' \
  -F 'use_ai=true' \
  -F 'stream=true'
```

`stream=true` 返回 `text/event-stream`，每条 `data:` 为 JSON，`type` 分别为 `status`、`summary`、`complete` 或 `error`。`summary.text` 为逐步增长的总体评价；`complete.report` 是最终完整报告。收到 `error` 或未收到 `complete` 就断开时，不应当作成功。默认 `stream=false` 保留原来的 JSON 返回方式，AI 上游调用仍使用流式读取。

如果前面加了 Nginx 反向代理，应为该接口设置 `proxy_buffering off;` 和足够长的 `proxy_read_timeout`；应用也会发送 `X-Accel-Buffering: no`。

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/` | GET | 中文操作界面 |
| `/api/status` | GET | 服务和 AI 配置状态 |
| `/api/sample` | GET | 示例简历与岗位 |
| `/api/analyze` | POST | 表单或文件分析，支持 SSE 实时返回 |
| `/docs` | GET | Swagger 交互文档 |

文件上限 8 MB；PDF 上限 30 页；简历正文 60—40000 字符；岗位描述上限 12000 字符。扫描 PDF 需先 OCR；旧版 DOC 需另存为 DOCX。

## 验证

```bash
.venv/bin/python -m unittest -v
```

测试覆盖技能边界、匹配分数、文本与 DOCX 表格解析、上传和长度限制、扫描 PDF 提示，以及模拟大模型流式成功、中文分段、输出无效、超时和中途断流。同时覆盖 PDF 换行后的原句匹配、数字字符串评分及可选改写异常。无法核实来源的单条改写会被省略，其他已校验的报告内容正常展示。测试不会调用外部大模型。

## 项目文件

```text
app.py              FastAPI 路由、文件解析、启动入口
analyzer.py         本地规则与大模型分析
static/             中文网页、样式和交互
samples/            示例简历与岗位描述
test_app.py         自动化测试
.env.example        可选的大模型配置模板
requirements.txt    Python 依赖
```

默认仅监听本机。开发时可使用 `.venv/bin/uvicorn app:app --reload --port 5000`。

需要局域网或 VPN 访问时，将 `.env` 中的 `HOST` 改为本机对应网卡的 IP，或使用 `0.0.0.0` 监听所有网卡，再通过服务器 IP 和端口访问。直接运行 `python app.py` 会读取此配置；使用 uvicorn 命令时需通过 `--host` 和 `--port` 指定地址。
