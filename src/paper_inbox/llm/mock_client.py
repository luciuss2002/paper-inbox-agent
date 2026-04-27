"""Mock LLM client for offline tests and `--mock-llm` CLI runs."""

from __future__ import annotations

import json
import re

from paper_inbox.llm.base import LLMClient, LLMResponse


def _extract_field(prompt: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*:\s*(.+)"
    m = re.search(pattern, prompt)
    return m.group(1).strip() if m else ""


def _heuristic_triage(prompt: str) -> dict:
    """Cheap deterministic triage scoring driven by keyword presence."""
    text = prompt.lower()

    high_kw = [
        "agentic reinforcement learning",
        "tool use",
        "search augmented reasoning",
        "search-augmented",
        "paper reading agent",
        "code agent",
        "reproducibility",
    ]
    medium_kw = ["lora", "long context", "rag", "workflow automation"]
    low_kw = ["pure computer vision", "pure robotics", "leaderboard"]

    high_hits = sum(1 for k in high_kw if k in text)
    medium_hits = sum(1 for k in medium_kw if k in text)
    low_hits = sum(1 for k in low_kw if k in text)

    relevance = max(1, min(5, 1 + high_hits * 2 + medium_hits - low_hits * 2))
    novelty = 4 if high_hits else (3 if medium_hits else 2)
    practicality = 4 if "code" in text or "release" in text else 3
    experiment_strength = 3
    reproducibility = 5 if ("code" in text and "release" in text) else (4 if "code" in text else 2)
    trend = 5 if high_hits else 3

    reasons = []
    if high_hits:
        reasons.append("Topic matches user's high-priority interests")
    if low_hits:
        reasons.append("Some signals overlap with the user's low-interest list")
    if "code" in text:
        reasons.append("Authors mention code release / strong reproducibility signal")
    if not reasons:
        reasons.append("Fallback heuristic: limited signal in title/abstract")

    action = (
        "精读核心方法部分并对比 baseline"
        if relevance >= 4
        else "扫一眼摘要决定是否归档"
    )

    return {
        "relevance_to_user": int(relevance),
        "novelty": int(novelty),
        "practicality": int(practicality),
        "experiment_strength": int(experiment_strength),
        "reproducibility_signal": int(reproducibility),
        "trend_signal": int(trend),
        "reasons": reasons,
        "recommended_action": action,
    }


def _mock_brief_markdown(title: str) -> str:
    title = title or "Untitled paper"
    return f"""# {title}

## 0. Verdict

一句话判断：基于 mock LLM 的占位 brief，仅用于离线测试。

优先级：Skim

建议动作：
- 精读 method 部分
- 跳过 related work

## 1. 30 秒版本

- 这篇论文研究的问题是 (mock placeholder)。
- 核心方法是 (mock placeholder)。
- 主要结论是 (mock placeholder)。

## 2. 3 分钟版本

### Problem
(mock)

### Key Idea
(mock)

### Method
(mock)

### Experiments
(mock)

### Main Takeaway
(mock)

## 3. 它和我的方向有什么关系？

- Agentic RL: 相关性 (mock)
- Tool use: 相关性 (mock)

## 4. 核心 claim

| Claim | Evidence | 可信度 | 是否值得验证 |
|---|---|---|---|
| (mock claim) | (mock evidence) | 中 | 是 |

## 5. 实验可信度

### 优点
(mock)

### 问题
(mock)

### 缺失实验
(mock)

## 6. 可借鉴点

- (mock)

## 7. 我的问题

- (mock)

## 8. 相关论文或关键词

- ReAct
- Toolformer
"""


def _mock_daily_report(prompt: str) -> str:
    return """# Daily Paper Inbox - mock

## Summary

(mock summary placeholder)

## 今日趋势

1. mock trend
2. mock trend

## Must Read

(mock must-read placeholder)

## Skim

(mock skim placeholder)

## Archive Highlights

(mock archive placeholder)

## 建议明天跟进

- (mock)
"""


class MockLLMClient(LLMClient):
    """Returns deterministic responses for triage / fast-read / daily-report prompts."""

    def __init__(self, *, model_name: str = "mock-llm") -> None:
        self.model_name = model_name

    async def complete(
        self, prompt: str, *, model: str, temperature: float = 0.2
    ) -> LLMResponse:
        kind = self._classify(prompt)
        if kind == "triage":
            text = json.dumps(_heuristic_triage(prompt), ensure_ascii=False)
        elif kind == "fast_reader":
            title = _extract_field(prompt, "Title")
            text = _mock_brief_markdown(title)
        else:
            text = _mock_daily_report(prompt)
        return LLMResponse(text=text, model=model or self.model_name, usage=None)

    @staticmethod
    def _classify(prompt: str) -> str:
        if "请输出严格 JSON" in prompt or "relevance_to_user" in prompt and "novelty" in prompt and "JSON" in prompt:
            return "triage"
        if "速读助手" in prompt or "fast reader" in prompt.lower():
            return "fast_reader"
        if "每日论文简报" in prompt or "daily" in prompt.lower():
            return "daily_report"
        return "fast_reader"
