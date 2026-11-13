# Agent Framework

一个在本机运行的多智能体工作台。你可以在浏览器中发起任务，查看对话、工具调用、任务拆解、执行指标和结果；后端负责把任务交给通用、研究、代码或办公智能体。

**项目说明页：** [sucpyy.github.io/agent-framework](https://sucpyy.github.io/agent-framework/) · **源码主页：** [github.com/sucpyy/agent-framework](https://github.com/sucpyy/agent-framework)

> 这是本地应用。GitHub Pages 展示项目说明，不运行 Python 后端或在线工作台。

## 能做什么

- **对话与任务执行：** 快速对话、任务拆解、智能体调度、流式进度、会话保存与恢复。
- **代码与文件：** 运行命令，读取、写入、编辑和搜索项目文件；相对路径统一从工作区根目录解析。
- **网络调研：** 在线搜索、抓取结果页；可按配置使用搜索服务，并在提供者失败时尝试后备来源。
- **办公处理：** 创建文档、保存邮件草稿、管理本地日程与待办；部分操作需要外部服务或桌面环境。
- **可观测性：** 展示工具调用、任务执行图、质量评估和系统状态。无法采集的指标会标为“不可用”。

## 快速开始

需要 **Python 3.10+**。Windows 建议安装 Chrome 或 Edge，以独立窗口打开工作台。

```powershell
git clone https://github.com/sucpyy/agent-framework.git
cd agent-framework
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

也可以在安装依赖后双击 [`启动.bat`](启动.bat)。启动器使用项目内的 `.venv`，默认监听 `127.0.0.1:8000`。如果没有自动打开窗口，访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。健康检查位于 `/health`。

首次启动会创建本地管理员：用户名 `admin`，密码 `admin123`。当前版本没有修改该账号密码的界面；应用只绑定本机地址，请勿直接暴露到公网或交给不受信任的本机用户使用。

没有模型密钥时，应用以 **Echo 模式**响应，可用于查看界面和基础流程；需要真实模型执行任务时，在工作台的“系统设置”中配置 API 密钥、模型和兼容接口地址，或在启动前设置 `DEEPSEEK_API_KEY`。不要把真实密钥写进源码或提交到 Git。

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
.\.venv\Scripts\python.exe app.py
```

## 智能体与工具

```text
用户请求 → FastAPI / WebSocket → 快速通道或任务规划
                                  ├─ 通用智能体：对话、文件、命令、搜索、待办、子任务
                                  └─ 调度器 → 研究 / 代码 / 办公智能体 → 结果汇总
```

| 智能体 | 主要职责 | 可用工具 |
| --- | --- | --- |
| `general_agent` | 通用对话、直接执行与委派 | `bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`web_search`、`search_and_fetch`、`todo_write`、`spawn_agent` |
| `research_agent` | 搜索、抓取与整理来源 | `web_search`、`search_and_fetch` |
| `code_agent` | 代码分析、修改和验证 | `bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`apply_changes` |
| `office_agent` | 邮件、文档、日程、翻译和剪贴板 | `email`、`document`、`calendar`、`translate`、`clipboard` |

工具的实际能力取决于环境与配置：

| 工具/数据 | 运行条件与落点 |
| --- | --- |
| 文件与命令 | 从项目根目录运行；`AGENT_WORKSPACE_ROOT` 可覆盖工作区根目录。Windows 优先使用 Git Bash，没有时使用 PowerShell。 |
| 搜索 | 可在系统设置中配置 Brave/Tavily 等搜索密钥；后备来源也依赖网络可用性。来源都失败时返回错误。 |
| 文档与邮件 | 文档写到 `documents/`；邮件默认保存到 `drafts/`。只有用户明确要求发送且 SMTP 已配置时才尝试发信。 |
| 日程与待办 | 保存在 `.workspace_data/`。 |
| 翻译与剪贴板 | 翻译需要可用的在线模型；剪贴板需要本机桌面会话。 |

邮件发送可通过 `SMTP_HOST`、`SMTP_PORT`、`SMTP_FROM`、`SMTP_USER`、`SMTP_PASS` 等环境变量配置。未配置 SMTP 时可保存草稿，但不能实际发送。在线搜索受第三方服务和网络连接影响，不能保证每次都有结果。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `app.py`、`启动.bat` | 本地启动与桌面窗口入口 |
| `server.py` | FastAPI 页面、REST 接口与 WebSocket |
| `static/` | 原生 HTML、CSS、JavaScript 工作台 |
| `framework/agent/` | 四类智能体 |
| `framework/skill/` | 文件、命令、搜索及办公工具 |
| `framework/task/`、`framework/scheduler/`、`framework/orchestrator/` | 任务规划、调度与执行 |
| `framework/session/`、`framework/memory/`、`framework/evaluation/` | 会话、记忆和评估 |
| `tests/` | 后端与前端回归测试 |
| `docs/` | GitHub Pages 项目说明页 |

运行时自动生成的 `.agent_settings.json`、`.auth/`、`.sessions/`、`.memory/`、`.workspace_data/`、`documents/` 和 `drafts/` 都属于本机数据，**不要提交**。若从旧版仓库升级，先备份这些目录，并在公开代码前检查完整提交历史。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/frontend.test.cjs
git diff --check
```

`node` 仅用于前端测试。也可运行 `python server.py` 启动网页服务；桌面入口 `app.py` 会尝试打开 Chrome/Edge 的独立窗口。此项目目前以本机单用户使用为主，内置账号和文件工具不适合未经额外安全加固的公网部署。

## 访问与反馈

- [GitHub 仓库主页](https://github.com/sucpyy/agent-framework)：完整 README、源码和测试。
- [项目说明页](https://sucpyy.github.io/agent-framework/)：可直接分享的静态说明页。
- 遇到问题时，先检查 `/health`、模型设置、工具调用日志，以及所需的网络或外部服务配置。
