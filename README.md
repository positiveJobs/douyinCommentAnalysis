# 抖音评论观点智能分析

一个把抖音评论变成「观点清单」的分析工具：上传评论 JSON，自动完成 **数据清洗 → LLM 语义提取 → 向量聚类 → 观点归并命名 → 理由归纳 → 统计 → 报告生成**，每个观点都能沿 `观点 → comment_id → 原始评论` 一路追溯。

> 数据来源：评论数据由 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 抓取。

## 功能

- 一条评论可以包含多个观点，自动拆分并归并语义相同的观点（不依赖关键词匹配）。
- 观点人数、占比、代表性评论由程序按 `comment_id` 去重统计，LLM 只负责语义理解，不参与计数。
- Web 界面支持上传、实时进度、取消分析、结果可视化，以及 HTML / Markdown / JSON / zip 打包下载。
- LLM 与 Embedding 结果落盘缓存并跨任务共享，重复分析同一份数据几乎不再消耗 API。
- 同时提供 CLI，与 Web 共用同一套分析引擎。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.12 | 由 `uv` 自动安装，无需手动准备 |
| [uv](https://docs.astral.sh/uv/) | Python 依赖与虚拟环境管理 |
| Node.js 18+（含 npm） | 构建前端界面 |
| OpenAI 兼容的 LLM 接口 | 需要 Base URL、API Key 与模型名 |
| 网络 | 首次运行自动下载本地向量模型 `bge-base-zh-v1.5`（约 100 MB） |

Windows 建议直接使用 `start.bat`；macOS / Linux 可按下方命令手动启动。

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/positiveJobs/douyinCommentAnalysis.git
cd douyinCommentAnalysis
```

### 2. 配置 LLM

复制配置模板并填入自己的接口信息：

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

`.env` 中至少填写：

```text
BASE_URL=https://your-llm-endpoint/v1/chat/completions
API_KEY=sk-your-key
MODEL_NAME=your-model-name
```

### 3. 启动

**Windows 一键启动（推荐）**

```bat
start.bat
```

首次运行会自动执行 `uv sync`、`npm install`、`npm run build`，完成后打开 <http://127.0.0.1:8000>。

```bat
start.bat dev     开发模式（后端热重载 + 前端 Vite 热更新，http://127.0.0.1:5173）
stop.bat          停止服务
start.bat help    查看全部命令
```

**macOS / Linux 手动启动**

```bash
uv sync
cd frontend && npm install && npm run build && cd ..
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

开发模式（两个终端）：

```bash
uv run uvicorn backend.main:app --reload   # 后端 http://127.0.0.1:8000
cd frontend && npm run dev                 # 前端 http://127.0.0.1:5173
```

## 使用流程

打开首页 → 拖入评论 JSON →（可选）填写讨论主题 → 开始分析 → 在进度页等待完成 → 查看观点分布、占比、代表性评论与完整报告，并按需下载。

上传文件格式（也支持 `{"topic": "...", "comments": [...]}`）：

```json
[
  { "comment_id": "7213000000000000000", "content": "这个功能确实好用" }
]
```

## 分析流程

```text
评论 JSON
  → 清洗 / 去重（Python）
  → LLM 批量提取观点（statement / stance / reason）
  → Embedding + 余弦相似度聚类
  → LLM 归并、命名观点
  → Python 统计人数与占比
  → LLM 归纳理由，Python 统计理由人数
  → analysis_results.json + Markdown / HTML 报告
```

## 命令行

```bash
uv run python run_pipeline.py all       # 全流程（结果会复用缓存）
uv run python run_pipeline.py report    # 单独重跑某个阶段，如 report / cluster / merge
```

CLI 默认读取 `data/samples/detail_comments.json`，输出写入 `data/cli/`。

## 目录结构

```text
analysis/    分析引擎（CLI 与 Web 共用）
backend/     FastAPI 服务（上传、任务调度、结果与下载）
frontend/    Vue 3 + Element Plus + ECharts 界面
tools/       结果检查与查看工具
data/        本地数据目录（已忽略，运行时自动创建）
cache/       LLM / Embedding 缓存（已忽略）
models/      本地向量模型（已忽略，首次运行自动下载）
```

## 说明

- `.env`、`data/`、`cache/`、`models/`、`frontend/node_modules/` 均不纳入版本库。
- Embedding 设备默认自动选择（CPU / CUDA / DirectML），可用 `.env` 中的 `EMBEDDING_DEVICE` 强制指定。
- 分析失败时可在后端日志窗口查看原因，常见问题是 `.env` 中的接口地址或 Key 配置有误。
