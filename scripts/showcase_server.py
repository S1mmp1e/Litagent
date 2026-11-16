"""Offline-only model stub for reproducible UI demonstration, not quality evaluation."""

import os
import re

assert ".notebook-test-showcase-" in os.environ["LIT_DB_PATH"]
assert not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("LIT_API_KEY")

from lit_agent import jobs, retrieval, store
from tests.personal_server import TestEncoder


class ShowcaseModel:
    def __init__(self, guard):
        self.model = "offline-showcase-fixture"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}

    def complete(self, messages, on_delta=None, **kwargs):
        self.usage["model_calls"] += 1
        content = messages[-1]["content"]
        citations = list(dict.fromkeys(re.findall(r"\[cite:([a-f0-9-]{36})\]", content)))
        citations = [key for key in citations if store.one(
            "SELECT id FROM chunks WHERE id=? AND page=1", (key,))]
        personal = list(dict.fromkeys(re.findall(r"\[personal:([a-f0-9-]{36})\]", content)))
        text = ("## 结合个人批注分析\n\n> 离线演示回答 · 用于验证来源区分与交互，不代表模型评测。\n\n"
                "### 论文材料中的信息条件\n\n图策略在执行阶段使用局部观测与邻居消息；"
                "训练时的全局 critic 不意味着执行时也能访问全局状态。")
        if citations:
            text += " ".join(f"[cite:{key}]" for key in citations[:2])
        if personal:
            text += ("\n\n### 你的研究条件\n\n你的批注要求只使用局部观测，并明确记录通信条件。"
                     f"这是个人研究约束，需要与原文的邻居消息假设逐项对照。[personal:{personal[0]}]")
        text += ("\n\n### 建议的验证顺序\n\n1. 明确 actor 每一步可访问的观测与消息。\n"
                 "2. 在相同训练预算下加入无通信对照。\n"
                 "3. 分别记录任务成功、消息量与时延。\n\n"
                 "以上是读者验证计划，示例材料没有提供可报告的实验分数。")
        if on_delta:
            on_delta(text)
        return {"role": "assistant", "content": text}

    def close(self):
        pass


def main():
    retrieval.get_embedder = lambda: TestEncoder()
    jobs.Model = ShowcaseModel
    from lit_agent.webui import main as serve
    serve()


if __name__ == "__main__":
    main()
