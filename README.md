<div align="center">

# 📚 Paper Inbox Agent

**每日论文漏斗：从论文源抓取 → 按你的研究兴趣打分分桶 → 对高优先级论文生成可行动的研究 brief**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-46aef7.svg)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-pytest-0a9edc.svg)](https://docs.pytest.org/)
[![Status](https://img.shields.io/badge/status-MVP-success.svg)](#-roadmap)

</div>

---

## 🤔 它解决什么问题

每天 arXiv 上 cs.CL / cs.AI / cs.LG 加起来有几百篇新论文。你不可能也不应该全读。

**Paper Inbox Agent** 是一个面向个人研究者的"论文秘书"：

- 它每天从 arXiv 等源拉取候选论文
- 按你在 `research_profile.yaml` 里写的兴趣方向打分（六个维度 1–5）
- 用规则把论文分到 **Must Read / Skim / Archive / Ignore** 四个桶
- 对 Must Read / Skim 论文下载 PDF、抽取文本、调用 LLM 生成结构化 brief
- 输出每日 Markdown 报告与可检索的 SQLite 库

它**不**自动复现实验，**不**运行陌生 repo 代码，**不**做长链推理 agent —— 就是把"读论文这件事的入口"做对。

## ✨ Features

- 🎯 **个性化打分**：六维度（relevance / novelty / practicality / experiment_strength / reproducibility / trend）+ 规则覆盖
- 📂 **自动分桶**：Must Read / Skim / Archive / Ignore，每天给出可执行清单
- 📝 **结构化 brief**：30 秒版 / 3 分钟版 / 与你方向的关联 / 核心 claim 表 / 可借鉴点
- 🔌 **可插拔 LLM**：抽象 `LLMClient`，自带 OpenAI 实现 + Mock 实现，支持任何 OpenAI 兼容端点
- 💾 **SQLite 持久化**：论文元信息、评分、brief、artifact、用户反馈全部入库
- 🧪 **离线可测**：内置 arXiv fixture + Mock LLM，无网即可跑通 end-to-end
- 🛡️ **默认安全**：不自动执行任何论文/repo 代码，不读取云凭证
- ⚙️ **配置即代码**：`research_profile.yaml` / `sources.yaml` / `runtime.yaml` 三段式配置

## 🚀 Quick Start

```bash
# 1) 安装
git clone https://github.com/<you>/paper-inbox-agent.git
cd paper-inbox-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2) 初始化（生成配置 + 创建 SQLite）
paper-inbox init

# 3) 离线跑通：Mock LLM + 内置 fixture，不需要网络也不需要 API key
paper-inbox run-daily --mock-llm \
  --offline-fixture tests/fixtures/sample_arxiv_feed.xml

# 4) 查看今日报告
cat data/reports/$(date +%F)/daily_brief.md
```

切换到真实 LLM：

```bash
pip install -e ".[openai]"
cp .env.example .env
# 在 .env 中填入 OPENAI_API_KEY=sk-...

paper-inbox run-daily          # 默认从 arXiv 拉取 + 调用真实模型
```

## 🧭 Pipeline

```
┌──────────┐   ┌────────┐   ┌────────┐   ┌────────────┐   ┌──────────┐
│  arXiv   │──▶│ collect│──▶│ dedupe │──▶│   triage   │──▶│  bucket  │
│  Atom    │   │        │   │        │   │ (LLM + rule│   │ Must Read│
└──────────┘   └────────┘   └────────┘   │  rescoring)│   │ Skim     │
                                         └────────────┘   │ Archive  │
                                                          │ Ignore   │
                                                          └────┬─────┘
                                                               │ priority ≥ 70
                                                               ▼
                                                        ┌────────────┐
                                                        │ pdf_fetch  │
                                                        │ pdf_parse  │
                                                        │ fast_read  │
                                                        └─────┬──────┘
                                                              ▼
                              ┌────────────────────────────────────────┐
                              │  data/reports/<date>/daily_brief.md    │
                              │  data/reports/<date>/must_read/*.md    │
                              │  data/reports/<date>/skim/*.md         │
                              │  data/paper_inbox.sqlite               │
                              └────────────────────────────────────────┘
```

## ⚙️ Configuration

`paper-inbox init` 会从 `configs/*.example.yaml` 复制出三份配置：

| 文件 | 作用 |
| --- | --- |
| `configs/research_profile.yaml` | 你的研究兴趣（high/medium/low）、最爱论文、负面样本、输出偏好 |
| `configs/sources.yaml` | 启用哪些论文源（默认只开 arXiv），分类与查询关键词 |
| `configs/runtime.yaml` | 数据目录、LLM provider/model、阈值（每日候选数、PDF 阅读上限等） |

环境变量参考 [`.env.example`](./.env.example)：

```env
OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://your-compatible-endpoint/v1
LOG_LEVEL=INFO
```

## 🧰 Commands

| 命令 | 作用 |
| --- | --- |
| `paper-inbox init` | 创建配置 + SQLite |
| `paper-inbox run-daily [--date Y-M-D] [--mock-llm] [--offline-fixture FILE]` | 跑完整每日流程 |
| `paper-inbox collect [--date ...]` | 仅采集 + 去重 + 入库 |
| `paper-inbox triage [--date ...] [--mock-llm]` | 仅对未打分论文做 LLM 打分 |
| `paper-inbox brief [--date ...] [--limit N] [--mock-llm]` | 对 N 篇高优先级论文生成 brief |
| `paper-inbox report [--date ...]` | 重新渲染并打印每日报告路径 |
| `paper-inbox list [--date ...] [--bucket "Must Read"]` | 列出当天已打分论文 |
| `paper-inbox feedback --paper-id arxiv:2501.12345 --feedback useful` | 记录用户反馈 |

`--feedback` 可选值：`useful` / `not_relevant` / `must_read` / `too_shallow` / `archive`。

## 📂 Project Layout

```
paper-inbox-agent/
├── configs/                 # research_profile / sources / runtime 三套 yaml
├── prompts/                 # triage / fast_reader / critic / daily_report 提示词
├── src/paper_inbox/
│   ├── cli.py               # Typer 入口
│   ├── settings.py          # 配置加载
│   ├── models.py            # Pydantic 数据模型
│   ├── db.py · migrations.py
│   ├── llm/                 # 抽象接口 + OpenAI / Mock 实现
│   ├── sources/             # arxiv_source.py · hf_daily_source.py
│   ├── pipeline/            # collect / dedupe / triage / pdf_* / fast_read / report
│   ├── scoring/             # 打分公式 + 桶分配 + 规则覆盖
│   ├── storage/             # 路径管理 + artifact 命名
│   └── utils/               # dates / hashing / text / retry
├── tests/                   # pytest 测试 + 离线 fixture
└── data/                    # 运行产生：pdfs/ parsed/ briefs/ reports/ paper_inbox.sqlite
```

## 🧪 Development

```bash
pip install -e ".[dev]"

ruff check .                  # 静态检查
ruff check . --fix            # 自动修复
pytest                        # 24 个测试全部离线运行
```

测试不依赖网络与 API key。CI 推荐配置：

```bash
pytest && ruff check .
```

## 🗺️ Roadmap

- [x] **v0.1 (MVP)** — arXiv + 打分分桶 + brief + 每日报告 + SQLite + CLI
- [ ] **v0.2** — Hugging Face Daily Papers 源、Semantic Scholar 元信息补全、用户反馈影响打分、Streamlit 看板
- [ ] **v0.3** — 本地向量检索、相关论文图谱、每周 trend report、按 topic 自动归档
- [ ] **v0.4** — 静态 repo inspector（只读）、复现可行性评估、claim ↔ code 对齐

## 🛡️ Safety

本项目**不会**：

- 自动执行论文附带代码或脚本
- `git clone` 并运行任意 repo
- 读取你的 SSH key、云凭证或浏览器 cookie
- 把本地文件发送到任何第三方（除非你显式配置 `OPENAI_BASE_URL` 指向第三方端点）

如果未来加入 repo 分析模块，也会保持只读，执行任何代码必须经过显式 sandbox + 人工审批。

## 🤝 Contributing

欢迎 issue 和 PR。本地开发请：

1. Fork & clone
2. 创建分支：`git checkout -b feature/your-thing`
3. 跑通 `pytest` 与 `ruff check .`
4. 提交 PR，描述动机与变更范围

新增功能优先满足设计原则（详见 [设计书](./Paper%20Inbox%20Agent%20设计书.md) §2）：先漏斗后深读、结果可行动、默认安全、可重复运行、prompt 与代码解耦。

## 📜 License

MIT — 详见 [LICENSE](./LICENSE)。
