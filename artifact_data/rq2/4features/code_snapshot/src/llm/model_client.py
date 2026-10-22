from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict

from src.common.text_utils import strip_code_fences


@dataclass
class ModelConfig:
    provider: str = "openai_compatible"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.0
    timeout_sec: int = 120
    max_retries: int = 3


class BaseModelClient:
    def chat_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raise NotImplementedError


class MockClient(BaseModelClient):
    def chat_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        if "Generate one Neo4j-only oracle testcase" in user_prompt:
            return {
                "setup_cypher": (
                    "CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'}); "
                    "CREATE (:Person {name:'Charlie'})"
                ),
                "target_query": (
                    "MATCH p = (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'}) "
                    "RETURN count(p) AS c"
                ),
                "spec_query_candidate": "RETURN 1 AS ok",
                "expected_result_shape": {
                    "keys": ["c"],
                    "ordered": True,
                    "kind": "single_count",
                },
                "oracleability_notes": [
                    "tiny deterministic setup",
                    "single exact count result",
                ],
            }
        if "Neo4j-only Cypher semantics oracle assistant" in system_prompt:
            return {
                "oracle_confidence": "high",
                "setup_graph": {
                    "nodes": [
                        {"labels": ["Person"], "props": {"name": "Alice"}},
                        {"labels": ["Person"], "props": {"name": "Bob"}},
                        {"labels": ["Person"], "props": {"name": "Charlie"}},
                    ],
                    "relationships": [
                        {"type": "KNOWS", "from": "Alice", "to": "Bob"},
                    ],
                },
                "clause_trace": [
                    {
                        "clause": "MATCH p = (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})",
                        "rows": [{"p": "path_1"}],
                        "visible_vars": ["p"],
                    },
                    {
                        "clause": "RETURN count(p) AS c",
                        "rows": [{"c": 1}],
                        "visible_vars": ["c"],
                    },
                ],
                "final_result": {
                    "keys": ["c"],
                    "records": [{"c": 1}],
                    "ordered": True,
                },
                "justification_rules": [
                    "single matching path exists",
                    "count over one row returns one",
                ],
            }
        if "independent verifier for a Neo4j-only Cypher oracle testcase" in system_prompt:
            return {
                "confidence": "high",
                "final_result": {
                    "keys": ["c"],
                    "records": [{"c": 1}],
                    "ordered": True,
                },
                "matches_primary_exactly": True,
            }
        return {
            "skeleton_text": "MATCH (VAR0) RETURN RET_EXPR_0",
            "holes": [
                {"name": "RET_EXPR_0", "type": "return_items", "original": "<mock>"}
            ],
            "feature_items": [
                {
                    "feature_name": "mock_feature",
                    "feature_description": "这是一个模拟特征，用于测试 Stage 1 流程。",
                    "query_instance": "MATCH (n) RETURN n",
                    "tags": ["mock"],
                    "feature_signature": ["MATCH", "RETURN"],
                }
            ],
            "notes": "mock response",
        }


class OpenAICompatibleClient(BaseModelClient):
    def __init__(self, cfg: ModelConfig):
        from openai import OpenAI

        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise EnvironmentError(
                f"Environment variable {cfg.api_key_env} is not set. "
                f"Please export/set it before running."
            )
        self.cfg = cfg
        self.client = OpenAI(api_key=api_key, base_url=cfg.base_url, timeout=cfg.timeout_sec)

    def chat_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        last_error = None
        for _ in range(max(1, self.cfg.max_retries)):
            try:
                response = self.client.chat.completions.create(
                    model=self.cfg.model,
                    temperature=self.cfg.temperature,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                content = response.choices[0].message.content or "{}"
                content = strip_code_fences(content)
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError("Model did not return a JSON object")
                return data
            except Exception as e:
                last_error = e
        raise last_error if last_error else RuntimeError("Unknown model client error")


def resolve_model_config(cfg_dict: Dict[str, Any], task: str | None = None) -> Dict[str, Any]:
    if "default" in cfg_dict or "tasks" in cfg_dict:
        base = dict(cfg_dict.get("default", {}))
        if task:
            base.update(cfg_dict.get("tasks", {}).get(task, {}))
        return base
    return dict(cfg_dict)


def create_model_client(cfg_dict: Dict[str, Any], task: str | None = None) -> BaseModelClient:
    resolved = resolve_model_config(cfg_dict, task=task)
    cfg = ModelConfig(**resolved)
    if cfg.provider == "mock":
        return MockClient()
    if cfg.provider == "openai_compatible":
        return OpenAICompatibleClient(cfg)
    raise ValueError(f"Unsupported provider: {cfg.provider}")
