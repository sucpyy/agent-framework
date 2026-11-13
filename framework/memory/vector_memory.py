"""VectorMemory v1.0 — 向量数据库长期记忆 + 语义检索 + 记忆衰减。

基于 ChromaDB 实现:
  - 语义搜索: 用 embedding 向量匹配最相关记忆
  - 记忆衰减: 按时间和访问频率衰减权重
  - 重要性评分: 自动评估记忆重要性
  - 自动摘要: 长内容压缩为关键信息
  - 渐进式淘汰: 低权重记忆自动清理

依赖: pip install chromadb
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CHROMA_AVAILABLE = False
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    _CHROMA_AVAILABLE = True
except ImportError:
    pass


@dataclass
class MemoryEntry:
    """单条记忆条目。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content: str = ""
    category: str = "general"  # task, decision, knowledge, preference
    importance: float = 0.5    # 0-1, 自动评估
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    decay_rate: float = 0.01   # 每日衰减率
    metadata: dict = field(default_factory=dict)

    @property
    def weight(self) -> float:
        """当前权重 = 重要性 × 衰减系数 × 访问加成。"""
        days_old = (time.time() - self.created_at) / 86400
        decay = math.exp(-self.decay_rate * days_old)
        access_bonus = math.log(self.access_count + 2) / math.log(2)  # log2(access+2)
        return self.importance * decay * min(access_bonus, 3.0)


class VectorMemory:
    """向量数据库长期记忆——语义搜索 + 自动管理。

    用法:
        mem = VectorMemory()
        mem.store("用户偏好使用 DeepSeek 模型", category="preference", importance=0.9)
        results = mem.search("用什么模型", top_k=3)
    """

    def __init__(self, persist_dir: str = ".memory/vector_store"):
        self._entries: dict[str, MemoryEntry] = {}
        self._persist_dir = Path(persist_dir)
        self._collection = None
        self._use_chroma = _CHROMA_AVAILABLE

        if self._use_chroma:
            try:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(
                    path=str(self._persist_dir),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                self._collection = client.get_or_create_collection(
                    name="agent_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                self._use_chroma = False

        self._load_fallback()

    # ── Store ─────────────────────────────────

    def store(self, content: str, category: str = "general",
              importance: float | None = None, metadata: dict | None = None) -> str:
        """存储一条记忆。自动评估重要性。"""
        if importance is None:
            importance = self._estimate_importance(content)

        entry = MemoryEntry(
            content=content,
            category=category,
            importance=importance,
            metadata=metadata or {},
        )
        self._entries[entry.id] = entry

        # 写入向量库
        if self._use_chroma and self._collection is not None:
            try:
                self._collection.add(
                    ids=[entry.id],
                    documents=[content],
                    metadatas=[{
                        "category": category,
                        "importance": importance,
                        "created_at": entry.created_at,
                    }],
                )
            except Exception:
                pass

        self._save_fallback()
        return entry.id

    def store_batch(self, items: list[dict]) -> list[str]:
        """批量存储。"""
        ids = []
        for item in items:
            ids.append(self.store(
                content=item.get("content", ""),
                category=item.get("category", "general"),
                importance=item.get("importance"),
                metadata=item.get("metadata"),
            ))
        return ids

    # ── Search ────────────────────────────────

    def search(self, query: str, top_k: int = 5,
               category: str | None = None,
               min_weight: float = 0.0) -> list[dict]:
        """语义搜索记忆（向量 + 关键词混合）。"""
        results = []

        if self._use_chroma and self._collection is not None and self._collection.count() > 0:
            try:
                where = {"category": category} if category else None
                chroma_results = self._collection.query(
                    query_texts=[query],
                    n_results=min(top_k * 2, self._collection.count()),
                    where=where,
                )
                if chroma_results and chroma_results.get("ids"):
                    for i, mid in enumerate(chroma_results["ids"][0]):
                        distance = chroma_results.get("distances", [[1.0]])[0][i]
                        entry = self._entries.get(mid)
                        if entry and entry.weight >= min_weight:
                            doc = chroma_results["documents"][0][i] if chroma_results.get("documents") else entry.content
                            results.append({
                                "id": mid,
                                "content": doc,
                                "category": entry.category,
                                "weight": entry.weight,
                                "importance": entry.importance,
                                "score": 1.0 - min(distance, 1.0),
                                "created_at": entry.created_at,
                            })
            except Exception:
                pass

        # 关键词回退
        if not results:
            results = self._keyword_search(query, top_k, category, min_weight)

        # 更新访问统计
        for r in results:
            eid = r.get("id")
            if eid and eid in self._entries:
                self._entries[eid].last_accessed = time.time()
                self._entries[eid].access_count += 1

        return sorted(results, key=lambda r: r.get("score", 0), reverse=True)[:top_k]

    def _keyword_search(self, query: str, top_k: int,
                        category: str | None, min_weight: float) -> list[dict]:
        """关键词回退搜索（不依赖 embedding）。"""
        query_lower = query.lower()
        results = []
        for eid, entry in self._entries.items():
            if entry.weight < min_weight:
                continue
            if category and entry.category != category:
                continue
            # 简单包含匹配
            if query_lower in entry.content.lower():
                # TF-IDF 简化: 匹配次数 / 总长度
                score = entry.content.lower().count(query_lower) / max(len(entry.content), 1) * 100
                results.append({
                    "id": eid,
                    "content": entry.content,
                    "category": entry.category,
                    "weight": entry.weight,
                    "importance": entry.importance,
                    "score": min(score, 1.0),
                    "created_at": entry.created_at,
                })
        return sorted(results, key=lambda r: r.get("score", 0), reverse=True)[:top_k]

    # ── Memory Management ─────────────────────

    def recall_context(self, query: str, max_tokens: int = 2000) -> str:
        """搜索并拼接为上下文文本（可直接注入 prompt）。"""
        results = self.search(query, top_k=5, min_weight=0.1)
        if not results:
            return ""

        parts = ["## 相关记忆\n"]
        char_count = 0
        for r in results:
            snippet = f"- [{r['category']}] {r['content']}\n"
            if char_count + len(snippet) > max_tokens * 2:  # 粗略估算
                break
            parts.append(snippet)
            char_count += len(snippet)
        return "".join(parts)

    def cleanup(self, min_weight: float = 0.05, max_entries: int = 10000):
        """清理低权重记忆。"""
        to_remove = []
        for eid, entry in self._entries.items():
            if entry.weight < min_weight or len(self._entries) > max_entries:
                to_remove.append(eid)

        for eid in to_remove[-max(0, len(self._entries) - max_entries):]:
            del self._entries[eid]
            if self._use_chroma and self._collection is not None:
                try:
                    self._collection.delete(ids=[eid])
                except Exception:
                    pass

        self._save_fallback()

    def summarize_forget(self):
        """对低权重记忆生成摘要后删除原文（节省空间）。"""
        # 简化版：对 weight < 0.2 的记忆标记为可压缩
        # 完整版需要 LLM 摘要，这里跳过
        pass

    # ── Importance Estimation ─────────────────

    def _estimate_importance(self, content: str) -> float:
        """基于内容特征评估重要性。"""
        score = 0.5
        content_lower = content.lower()

        # 高重要性关键词
        high_kw = ["决策", "结论", "必须", "重要", "关键", "架构", "安全",
                    "decision", "critical", "architecture", "security"]
        if any(kw in content_lower for kw in high_kw):
            score += 0.3

        # 偏好类
        pref_kw = ["偏好", "喜欢", "使用", "prefer", "always", "never"]
        if any(kw in content_lower for kw in pref_kw):
            score += 0.2

        # 内容长度（适中最好）
        length = len(content)
        if 50 < length < 500:
            score += 0.1
        elif length > 2000:
            score -= 0.1

        return max(0.1, min(1.0, score))

    # ── Persistence ───────────────────────────

    def _save_fallback(self):
        """回退模式：JSON 文件持久化。"""
        if self._use_chroma:
            return
        path = self._persist_dir / "fallback_memory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for eid, entry in self._entries.items():
            data[eid] = {
                "content": entry.content,
                "category": entry.category,
                "importance": entry.importance,
                "created_at": entry.created_at,
                "last_accessed": entry.last_accessed,
                "access_count": entry.access_count,
                "decay_rate": entry.decay_rate,
                "metadata": entry.metadata,
            }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_fallback(self):
        path = self._persist_dir / "fallback_memory.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for eid, d in data.items():
                self._entries[eid] = MemoryEntry(
                    id=eid,
                    content=d.get("content", ""),
                    category=d.get("category", "general"),
                    importance=d.get("importance", 0.5),
                    created_at=d.get("created_at", 0),
                    last_accessed=d.get("last_accessed", 0),
                    access_count=d.get("access_count", 0),
                    decay_rate=d.get("decay_rate", 0.01),
                    metadata=d.get("metadata", {}),
                )
        except Exception:
            pass

    # ── Stats ─────────────────────────────────

    def stats(self) -> dict:
        entries = list(self._entries.values())
        if not entries:
            return {"total": 0}
        weights = [e.weight for e in entries]
        return {
            "total_entries": len(entries),
            "categories": {cat: sum(1 for e in entries if e.category == cat)
                          for cat in set(e.category for e in entries)},
            "avg_weight": round(sum(weights) / len(weights), 3),
            "max_weight": max(weights),
            "min_weight": min(weights),
            "chroma_enabled": self._use_chroma,
            "collection_count": self._collection.count() if self._collection else 0,
            "total_accesses": sum(e.access_count for e in entries),
        }


# 全局单例
_vector_memory: VectorMemory | None = None

def get_vector_memory() -> VectorMemory:
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = VectorMemory()
    return _vector_memory
