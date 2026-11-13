"""Model Router v1.0 — 多模型路由：根据角色自动选择最优模型。

策略:
  Orchestrator → 强推理模型 (Claude Opus / DeepSeek R1)
  Worker       → 性价比模型 (Claude Haiku / DeepSeek V3 / GPT-4o-mini)
  Evaluator    → 轻量模型 (DeepSeek V3 / GPT-4o-mini)
  Fallback     → 通用模型 (DeepSeek V3)

支持:
  - 按角色配置模型、端点、API Key
  - 动态热切换（Settings 变更实时生效）
  - 成本估算（token 价格追踪）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from ..settings import get_settings


@dataclass
class ModelConfig:
    """单个模型配置。"""
    model: str
    base_url: str
    api_key: str
    provider: str  # deepseek | openai | anthropic
    temperature: float = 0.3
    max_tokens: int = 4096
    # 成本估算 (per 1M tokens, USD)
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0


# 默认模型定价 (per 1M tokens)
MODEL_PRICES = {
    "deepseek-chat":        (0.14, 0.28),
    "deepseek-reasoner":    (0.55, 2.19),
    "deepseek-v4-pro":      (0.28, 0.56),
    "gpt-4o":              (2.50, 10.00),
    "gpt-4o-mini":          (0.15, 0.60),
    "claude-sonnet-4-6":   (3.00, 15.00),
    "claude-opus-4-6":     (15.00, 75.00),
    "claude-haiku-4-6":    (0.80, 4.00),
}


class ModelRouter:
    """多模型路由器——按角色分配最优模型。"""

    # 角色默认模型
    ROLE_DEFAULTS = {
        "orchestrator": "deepseek-reasoner",   # 强推理
        "worker":       "deepseek-chat",        # 性价比
        "evaluator":    "deepseek-chat",        # 轻量
    }

    def __init__(self):
        self._configs: dict[str, ModelConfig] = {}
        self._refresh()

    def _refresh(self):
        """从 Settings 刷新模型配置。"""
        settings = get_settings()
        base = settings._data.get("llm", {})

        # 默认配置（主模型）
        default_model = base.get("model", "deepseek-chat")
        default_url = base.get("base_url", "https://api.deepseek.com/v1")
        default_key = base.get("api_key", "")
        default_temp = base.get("temperature", 0.3)
        default_max_tok = base.get("max_tokens", 4096)

        # 多模型专用配置（settings 中的 "models" 段）
        models_cfg = settings._data.get("models", {})
        multi_enabled = models_cfg.get("enabled", False)

        for role, default_role_model in self.ROLE_DEFAULTS.items():
            role_cfg = models_cfg.get(role, {}) if multi_enabled else {}
            model_name = role_cfg.get("model", default_role_model)
            base_url = role_cfg.get("base_url", default_url)
            api_key = role_cfg.get("api_key", default_key)

            # 推断 provider
            provider = "deepseek"
            if (base_url and "openai.com" in base_url) or model_name.startswith("gpt"):
                provider = "openai"
            elif (base_url and "anthropic.com" in base_url) or model_name.startswith("claude"):
                provider = "anthropic"

            in_cost, out_cost = MODEL_PRICES.get(model_name, (0, 0))

            self._configs[role] = ModelConfig(
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                provider=provider,
                temperature=role_cfg.get("temperature", default_temp),
                max_tokens=role_cfg.get("max_tokens", default_max_tok),
                input_cost_per_1m=in_cost,
                output_cost_per_1m=out_cost,
            )

        # 兜底：未配置的角色用主模型
        for role in self.ROLE_DEFAULTS:
            if role not in self._configs:
                self._configs[role] = ModelConfig(
                    model=default_model,
                    base_url=default_url,
                    api_key=default_key,
                    provider="deepseek",
                )

    def get_config(self, role: str) -> ModelConfig:
        """获取指定角色的模型配置。"""
        self._refresh()
        return self._configs.get(role, self._configs.get("worker",
            ModelConfig(model="deepseek-chat", base_url="https://api.deepseek.com/v1",
                        api_key="", provider="deepseek")))

    def estimate_cost(self, role: str, input_tokens: int, output_tokens: int) -> float:
        """估算单次调用的成本 (USD)。"""
        cfg = self.get_config(role)
        return (input_tokens / 1_000_000 * cfg.input_cost_per_1m +
                output_tokens / 1_000_000 * cfg.output_cost_per_1m)

    def list_configs(self) -> dict[str, dict]:
        """列出所有角色的模型配置摘要。"""
        result = {}
        for role, cfg in self._configs.items():
            result[role] = {
                "model": cfg.model,
                "provider": cfg.provider,
                "base_url": cfg.base_url,
                "temperature": cfg.temperature,
                "input_cost_per_1m": cfg.input_cost_per_1m,
                "output_cost_per_1m": cfg.output_cost_per_1m,
            }
        return result

    def is_multi_model_enabled(self) -> bool:
        """是否启用了多模型路由。"""
        settings = get_settings()
        return settings._data.get("models", {}).get("enabled", False)


# 全局单例
_router: ModelRouter | None = None

def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
