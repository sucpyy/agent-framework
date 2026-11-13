"""Schema Validator — 校验 Worker 输出是否符合 Skill contract.produces。

纯代码，不调 LLM。格式不符合的直接拦截，不传给下游。
"""

from __future__ import annotations

from typing import Any


class ValidationError(Exception):
    """Schema 校验失败。"""
    def __init__(self, field: str, expected: str, got: str):
        self.field = field
        self.expected = expected
        self.got = got
        super().__init__(f"Validation error on '{field}': expected {expected}, got {got}")


class SchemaValidator:
    """校验 Worker 输出结构是否符合 contract。"""

    def __init__(self, strict: bool = False):
        self.strict = strict  # strict=True 时缺少字段直接抛异常
        self.errors: list[ValidationError] = []

    def validate(
        self,
        result: dict[str, Any],
        expected_fields: list[str],
        result_id: str = "",
    ) -> bool:
        """校验 result 是否包含所有 expected_fields。

        Returns:
            True 如果通过校验，False 如果有缺失。
        """
        self.errors.clear()
        missing = [f for f in expected_fields if f not in result]

        if missing:
            for field in missing:
                err = ValidationError(
                    field=field,
                    expected="present",
                    got="missing",
                )
                self.errors.append(err)

            if self.strict:
                raise self.errors[0]
            return False

        # 检查空值
        for field in expected_fields:
            value = result.get(field)
            if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
                err = ValidationError(
                    field=field,
                    expected="non-empty",
                    got=f"empty {type(value).__name__}",
                )
                self.errors.append(err)

        return len(self.errors) == 0

    def validate_batch(
        self,
        results: list[dict[str, Any]],
        expected_fields: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """批量校验，返回 (passes, failures)。"""
        passes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for i, result in enumerate(results):
            rid = f"result_{i}"
            if self.validate(result, expected_fields, rid):
                passes.append(result)
            else:
                failures.append({
                    "index": i,
                    "result": result,
                    "errors": [str(e) for e in self.errors],
                })

        return passes, failures
