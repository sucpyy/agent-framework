"""Structured Merger — 按 schema 合并多个 Worker 结果。

纯代码合并，不调 LLM。
去重、排序、按字段归并。
"""

from __future__ import annotations

from typing import Any


class StructuredMerger:
    """按 schema 合并 Worker 结果——纯确定性逻辑。"""

    def merge(
        self,
        results: list[dict[str, Any]],
        merge_strategy: str = "append",
    ) -> dict[str, Any]:
        """合并多个结果。

        Args:
            results: 已通过 SchemaValidator 的结果列表。
            merge_strategy:
                - "append": 列表字段追加合并
                - "concat": 字符串字段拼接合并
                - "pick_best": 按 relevance_score 选最高分

        Returns:
            合并后的单个结构化结果。
        """
        if not results:
            return {}

        if len(results) == 1:
            return dict(results[0])

        if merge_strategy == "pick_best":
            return self._pick_best(results)

        return self._deep_merge(results, merge_strategy)

    def _pick_best(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """按 relevance_score 选最优。"""
        best = results[0]
        best_score = best.get("relevance_score", 0)

        for r in results[1:]:
            score = r.get("relevance_score", 0)
            if score > best_score:
                best = r
                best_score = score

        return dict(best)

    def _deep_merge(
        self,
        results: list[dict[str, Any]],
        strategy: str,
    ) -> dict[str, Any]:
        """深度合并多个 dict。"""
        merged: dict[str, Any] = {}

        all_keys = set()
        for r in results:
            all_keys.update(r.keys())

        for key in all_keys:
            values = [r.get(key) for r in results if key in r]

            if not values:
                continue

            if all(isinstance(v, list) for v in values):
                # 列表：去重合并
                flat: list[Any] = []
                seen: set[str] = set()
                for lst in values:
                    for item in lst:
                        item_str = str(item)
                        if item_str not in seen:
                            flat.append(item)
                            seen.add(item_str)
                merged[key] = flat

            elif all(isinstance(v, str) for v in values):
                if strategy == "concat":
                    merged[key] = "\n\n".join(values)
                else:
                    # 取最长的（通常信息最丰富）
                    merged[key] = max(values, key=len)

            elif all(isinstance(v, (int, float)) for v in values):
                # 数值：取平均
                merged[key] = sum(values) / len(values)

            else:
                # 其他类型：取第一个非 None
                merged[key] = next((v for v in values if v is not None), None)

        return merged

    def merge_by_type(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """按 type 字段分组。"""
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            rtype = r.get("type", "unknown")
            groups.setdefault(rtype, []).append(r)
        return groups
