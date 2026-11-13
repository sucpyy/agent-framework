"""Settings Manager — 可持久化的配置系统

支持:
  - LLM 模型切换 (deepseek-chat, deepseek-reasoner, gpt-4o, claude-sonnet-4-6 等)
  - API Key 管理 (Tavily, Brave, Bing, SerpAPI)
  - 搜索源优先级配置
  - 所有配置通过 API 动态修改，实时生效
"""

import json, os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CONFIG = {
    "llm": {
        "provider": "deepseek",       # deepseek | openai | anthropic | custom
        "model": "deepseek-chat",     # 默认模型名
        "base_url": "https://api.deepseek.com/v1",  # API 端点（支持本地代理）
        "api_key": "",                # 留空则用环境变量
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    # v1.0: 多模型路由配置
    "models": {
        "enabled": False,             # 启用多模型路由
        "orchestrator": {             # 强推理模型（任务拆解）
            "model": "deepseek-reasoner",
            "base_url": "",
            "api_key": "",
        },
        "worker": {                   # 性价比模型（执行任务）
            "model": "deepseek-chat",
            "base_url": "",
            "api_key": "",
        },
        "evaluator": {                # 轻量模型（评分）
            "model": "deepseek-chat",
            "base_url": "",
            "api_key": "",
        },
    },
    "search": {
        "tavily_api_key": "",
        "brave_api_key": "",
        "bing_api_key": "",
        "serpapi_api_key": "",
        "jina_enabled": True,         # Jina Reader 免费抓取
        "fallback_order": ["baidu", "brave"],
    },
    "memory": {
        "vector_enabled": False,      # 启用向量记忆
        "persist_dir": ".memory/vector_store",
    },
    "ui": {
        "language": "zh",
        "theme": "dark",
    }
}


class Settings:
    def __init__(self, file_path: str = ".agent_settings.json"):
        self._path = Path(file_path)
        self._data: dict = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                # 深度合并（保留新增的默认字段）
                self._deep_merge(self._data, loaded)
            except (json.JSONDecodeError, FileNotFoundError):
                pass

    def save(self):
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    # ── Accessors ───────────────────────

    @property
    def llm_model(self) -> str:
        return self._data["llm"]["model"]

    @property
    def llm_provider(self) -> str:
        return self._data["llm"]["provider"]

    @property
    def llm_base_url(self) -> str:
        return self._data["llm"]["base_url"]

    @property
    def llm_api_key(self) -> str:
        return self._data["llm"]["api_key"] or os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def llm_temperature(self) -> float:
        return self._data["llm"]["temperature"]

    @property
    def llm_max_tokens(self) -> int:
        return self._data["llm"]["max_tokens"]

    def get_search_key(self, provider: str) -> str:
        return self._data["search"].get(f"{provider}_api_key", "")

    @property
    def search_fallback_order(self) -> list:
        return self._data["search"].get("fallback_order", ["tavily","jina","duckduckgo","direct"])

    @property
    def jina_enabled(self) -> bool:
        return self._data["search"].get("jina_enabled", True)

    # ── Mutators ────────────────────────

    def update_llm(self, **kw):
        # Filter out None values to avoid overwriting with null
        clean = {k: v for k, v in kw.items() if v is not None}
        self._data["llm"].update(clean)
        self.save()

    def update_search(self, **kw):
        self._data["search"].update(kw)
        self.save()

    def get_all(self) -> dict:
        return dict(self._data)

    def update_section(self, section: str, data: dict):
        if section in self._data:
            self._data[section].update(data)
            self.save()


# 全局单例
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
