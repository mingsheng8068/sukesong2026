"""设备知识库检索：维修文档 + 历史案例（同事按 docs/数据格式_给同事.md 编写，标明自编）。

- 文档 data/kb/docs/*.md：始终可见；
- 案例 data/kb/cases/*.md：每个文件是一份不可变快照，updated_at（这份正文和审核状态写定的时间）≤ as_of 才可见，
  没到就整条看不到（不能只把 verified 改成 false，却把后来填的根因也返回）；同一 id 有几份快照时取 as_of 前最新的一份；
  verified 为 false 的案例不能用来肯定或排除根因（契约会检查）。
检索用 BM25 + jieba 中文分词（requirements 里已锁版本），不用向量模型。文件放进来就能检索，不用重启。
知识库只有十几篇，用 BM25Plus（小语料下 BM25Okapi 的 idf 会变成负数）；至少命中一个查询词才算结果。
"""

from __future__ import annotations

import logging
from pathlib import Path

import jieba
import pandas as pd
from rank_bm25 import BM25Plus

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "kb"
TOP_K_MAX = 5
EXCERPT_CHARS = 600
TIME = "%Y-%m-%d %H:%M"

jieba.setLogLevel(logging.WARNING)


def parse_markdown(path: Path) -> dict:
    """读 front matter（--- 之间的 key: value，[a, b] 当列表，true / false 当布尔）和正文。"""
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        _, header, body = text.split("---", 2)
        for line in header.strip().splitlines():
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            if value.startswith("[") and value.endswith("]"):
                meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            elif value.lower() in ("true", "false"):
                meta[key] = value.lower() == "true"
            else:
                meta[key] = value
    if "id" not in meta:
        raise ValueError(f"{path.name} 缺少 id，格式见 docs/数据格式_给同事.md")
    meta["body"] = body.strip()
    return meta


def load_documents(kb_dir: Path | None = None) -> list[dict]:
    kb_dir = KB_DIR if kb_dir is None else kb_dir
    docs, keys = [], set()
    for kind, folder in (("文档", "docs"), ("案例", "cases")):
        for path in sorted((kb_dir / folder).glob("*.md")):
            doc = parse_markdown(path)
            doc["kind"] = kind
            if kind == "案例":
                if not {"created_at", "updated_at"} <= doc.keys() or not isinstance(doc.get("verified"), bool):
                    raise ValueError(f"{path.name} 要有 created_at、updated_at 和 verified: true / false")
                doc["created_at"], doc["updated_at"] = pd.Timestamp(doc["created_at"]), pd.Timestamp(doc["updated_at"])
                if doc["updated_at"] < doc["created_at"]:
                    raise ValueError(f"{path.name} 的 updated_at 早于 created_at")
            key = (doc["id"], doc.get("updated_at"))
            if key in keys:
                raise ValueError(f"{path.name}：id {doc['id']} 重复；同一案例的几份快照要写不同的 updated_at")
            keys.add(key)
            docs.append(doc)
    return docs


def visible_documents(docs: list[dict], as_of: pd.Timestamp) -> list[dict]:
    """文档全部可见；案例只取 updated_at ≤ as_of 的快照，同一 id 取最新的一份。"""
    latest: dict[str, dict] = {}
    for case in (d for d in docs if d["kind"] == "案例" and d["updated_at"] <= as_of):
        if case["id"] not in latest or case["updated_at"] > latest[case["id"]]["updated_at"]:
            latest[case["id"]] = case
    return [d for d in docs if d["kind"] == "文档"] + list(latest.values())


def _tokens(text: str) -> list[str]:
    return [t for t in jieba.lcut_for_search(text.lower()) if t.strip()]


def _searchable(doc: dict) -> str:
    symptoms = doc.get("symptoms", [])
    return " ".join([doc.get("title", ""), doc.get("equipment_type", ""), doc.get("component", ""),
                     " ".join(symptoms) if isinstance(symptoms, list) else str(symptoms), doc["body"]])


def search_knowledge(query: str, as_of: pd.Timestamp, top_k: int = 3) -> dict:
    """按描述检索知识文档和 as_of 时已写定的案例快照。相似只说明描述相近，不能证明是同一原因。"""
    if not isinstance(top_k, int) or not 1 <= top_k <= TOP_K_MAX:
        return {"found": False, "message": f"top_k 要在 1 到 {TOP_K_MAX} 之间"}
    docs = load_documents()
    if not docs:
        return {"found": False, "message": "知识库还没接入（data/kb/ 下的文档和案例待补）"}
    visible = visible_documents(docs, as_of)
    query_tokens = _tokens(query)
    if not visible or not query_tokens:
        return {"query": query, "results": [], "note": "没有可检索的内容"}
    corpus = [_tokens(_searchable(d)) for d in visible]
    scores = BM25Plus(corpus).get_scores(query_tokens)
    hits = [(score, doc) for score, doc, words in zip(scores, visible, corpus) if set(query_tokens) & set(words)]
    results = []
    for score, doc in sorted(hits, key=lambda pair: pair[0], reverse=True)[:top_k]:
        item = {"id": doc["id"], "kind": doc["kind"], "title": doc.get("title", ""),
                "equipment_type": doc.get("equipment_type", ""), "component": doc.get("component", ""),
                "score": round(float(score), 2), "excerpt": doc["body"][:EXCERPT_CHARS]}
        if doc["kind"] == "案例":
            item.update(verified=doc["verified"], created_at=f"{doc['created_at']:{TIME}}",
                        updated_at=f"{doc['updated_at']:{TIME}}")
        else:
            item["source"] = doc.get("source", "自编")
        results.append(item)
    return {"query": query, "results": results,
            "note": "相似只说明描述相近，不能证明是同一原因；verified 为 false 的案例不能用来肯定或排除根因"}
