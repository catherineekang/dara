"""Live backend over the Claude Code CLI in headless mode (`claude -p`).

Used for the final-report experiments when no OpenAI key was available: each
agent call is one `claude -p` invocation, billed to the operator's Claude
subscription rather than to an API key. It implements the same `Backend`
protocol as `OpenAIBackend`, so the pipeline, the prompts and the schemas are
unchanged; only the model behind them differs.

Differences from the OpenAI path, stated so the report can carry them:

* **Model.** `claude-sonnet-5-5` by default (`CLAUDE_CLI_MODEL` overrides).
* **Temperature.** The CLI exposes no sampling temperature, so every role
  (debaters, judge, recommender, evaluator) samples at the service default.
  Repeats therefore measure the model's natural spread, and the judge's
  self-consistency test is a harder test than it would be at temperature 0.
* **Structured output.** `--json-schema` with the role's Pydantic schema; the
  reply is validated against the same model, exactly as `with_structured_output`
  does on the OpenAI path. A reply that fails is asked for once more.
* **Isolation.** No tools, no MCP servers, no settings files, no session
  persistence, and a scratch working directory, so no repository context
  (CLAUDE.md, memory) can reach an agent. The role's system prompt replaces the
  CLI's own.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Type

DEFAULT_MODEL = os.getenv("CLAUDE_CLI_MODEL", "sonnet")
CALL_TIMEOUT_S = 600
RETRIES = 2


@dataclass
class ClaudeCLIBackend:
    model: str = DEFAULT_MODEL
    binary: str = "claude"
    usage: Dict[str, Any] = field(default_factory=lambda: {
        "input": 0, "output": 0, "calls": 0, "cost_microusd": 0, "ms": 0})
    is_cached: bool = False
    resolved_model: str = ""

    def __post_init__(self) -> None:
        self._cwd = tempfile.mkdtemp(prefix="dara_claude_")

    def _call(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        cmd = [self.binary, "-p", "--model", self.model,
               "--system-prompt", system,
               "--tools", "", "--strict-mcp-config", "--setting-sources", "",
               "--no-session-persistence", "--output-format", "json",
               "--json-schema", json.dumps(schema)]
        started = time.time()
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True,
                              cwd=self._cwd, timeout=CALL_TIMEOUT_S)
        self.usage["ms"] += int((time.time() - started) * 1000)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr[-400:]}")
        out = json.loads(proc.stdout)
        self.usage["calls"] += 1
        u = out.get("usage", {}) or {}
        self.usage["input"] += int(u.get("input_tokens", 0) or 0) + \
            int(u.get("cache_creation_input_tokens", 0) or 0) + \
            int(u.get("cache_read_input_tokens", 0) or 0)
        self.usage["output"] += int(u.get("output_tokens", 0) or 0)
        # list-price equivalent reported by the CLI; a subscription is not billed per call
        self.usage["cost_microusd"] += int(1e6 * float(out.get("total_cost_usd", 0.0) or 0.0))
        if not self.resolved_model and out.get("modelUsage"):
            self.resolved_model = next(iter(out["modelUsage"]))
        if out.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {str(out.get('result'))[:400]}")
        return out

    def parse(self, *, system: str, user: str, output_format: Type, max_tokens: int = 16000,
              tag: Optional[str] = None):
        schema = output_format.model_json_schema()
        last: Exception = RuntimeError("no attempt made")
        for attempt in range(1 + RETRIES):
            try:
                out = self._call(system, user, schema)
                payload = out.get("structured_output")
                if payload is None:
                    payload = json.loads(out.get("result") or "null")
                return output_format.model_validate(payload)
            except Exception as exc:  # malformed reply, rate limit, timeout
                last = exc
                self.usage["format_retries"] = self.usage.get("format_retries", 0) + 1
                time.sleep(5 * (attempt + 1))
        raise ValueError(f"claude -p reply did not fit {output_format.__name__} after "
                         f"{1 + RETRIES} attempts: {last}")
