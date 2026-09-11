"""评测集动态 few-shot 检索池（BM25 + jieba 分词）。

把评测集建成 (query -> tool) 池，对每个新 query 用 BM25 检索最近的前 K 个样本，
注入 select 提示词，让模型参考“已标注好的同类问句”选工具。

检索时排除与当前 query 完全相同的样本（同文本命中相似度最高，会泄漏答案）。

BM25：经典 Okapi，接口对标 rank_bm25（fit/candidates/get_scores），避免额外依赖。
分词用 jieba（本项目依赖已含），对中文短查询比 bigram 稀疏匹配更准。
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import jieba

logger = logging.getLogger("hcTools.examples")

_k1 = 1.5
_b = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip() and t.strip() != " "]


class BM25:
    """最小 BM25 索引：fit(corpus) 后给定查询返回文档得分。"""

    def __init__(self, corpus: list[str], k1: float = _k1, b: float = _b):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.doc_freqs: list[dict[str, int]] = []
        self.idf: dict[str, float] = {}
        self.doc_len: list[int] = []
        self.avgdl = 0.0
        self.data = [tokenize(doc) for doc in corpus]
        self._build()

    def _build(self) -> None:
        doc_freq: dict[str, int] = {}
        for d in self.data:
            seen: set[str] = set()
            for term in d:
                if term not in seen:
                    doc_freq[term] = doc_freq.get(term, 0) + 1
                    seen.add(term)
        N = len(self.data)
        self.idf = {
            term: math.log(1 + (N - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }
        self.doc_len = [len(d) for d in self.data]
        self.avgdl = (sum(self.doc_len) / N) if N else 0.0

    def get_scores(self, query: str) -> list[float]:
        """对每个 document 计算 BM25 得分。"""
        q_terms = tokenize(query)
        if not q_terms or not self.avgdl:
            return [0.0] * len(self.data)
        k1, b = self.k1, self.b
        scores = [0.0] * len(self.data)
        for idx, d in enumerate(self.data):
            dl = self.doc_len[idx]
            normalization = k1 * (1 - b + b * dl / self.avgdl)
            for term in q_terms:
                if term not in self.idf:
                    continue
                tf = 0
                for t in d:
                    if t == term:
                        tf += 1
                if not tf:
                    continue
                scores[idx] += self.idf[term] * (tf * (k1 + 1)) / (tf + normalization)
        return scores

    def get_top_n(self, query: str, n: int, exclude_ids: set[int] | None = None) -> list[tuple[int, float]]:
        """返回 [(doc_idx, score)]，按分数降序，排除 exclude_ids。"""
        scores = self.get_scores(query)
        ranked = sorted(
            ((i, s) for i, s in enumerate(scores)
             if s > 0 and (not exclude_ids or i not in exclude_ids)),
            key=lambda x: x[1], reverse=True,
        )
        return ranked[:n]


class ExampleBank:
    """评测集动态 few-shot 池：select 阶段用 BM25 检索最近似样本。"""

    def __init__(self, records: list[dict] | None = None):
        # 每项: {query, tool}
        self.all: list[dict] = []
        self.by_tool: dict[str, list[int]] = {}   # tool -> 记录下标列表
        for rec in records or []:
            q = rec.get("query")
            tool = rec.get("expected_tool") or (rec.get("tool_call") or {}).get("name")
            if not q or not tool:
                continue
            self.all.append({"query": str(q).strip(), "tool": str(tool)})
        # BM25 索引覆盖全池
        self._idx = None
        if self.all:
            self._idx = BM25([item["query"] for item in self.all])
            self._refresh_by_tool()

    def _refresh_by_tool(self) -> None:
        self.by_tool = {}
        for i, item in enumerate(self.all):
            self.by_tool.setdefault(item["tool"], []).append(i)

    def pick_tools(self, query: str, names: set[str], limit: int = 6,
                   exclude_self: bool = True) -> list[tuple[str, str]]:
        """检索 top-K 且工具在候选里的样本，返回 [(query, tool)]。"""
        if not self.all or self._idx is None:
            return []
        q = query.strip()
        if exclude_self:
            cand_ids = {i for i, item in enumerate(self.all) if item["tool"] in names and item["query"] != q}
        else:
            cand_ids = {i for i, item in enumerate(self.all) if item["tool"] in names}
        if not cand_ids:
            return []
        # 全池检索后过滤到候选为工具集内的样本
        ranked = self._idx.get_top_n(q, limit * 4)
        picked = []
        seen = set()
        for i, score in ranked:
            if i not in cand_ids or i in seen:
                continue
            picked.append((self.all[i]["query"], self.all[i]["tool"]))
            seen.add(i)
            if len(picked) >= limit:
                break
        return picked

    @classmethod
    def from_testset(cls, path: str | Path) -> "ExampleBank":
        """从评测集 JSON（含 records 列表）构建。"""
        p = Path(path)
        if not p.exists():
            logger.warning("评测集不存在：%s", p)
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # noqa: BLE001
            logger.error("评测集解析失败 %s: %s", p, exc)
            return cls()
        return cls(data.get("records"))


def build_bank_from_testset(path: str | Path) -> ExampleBank:
    return ExampleBank.from_testset(path)