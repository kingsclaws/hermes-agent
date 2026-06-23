"""Curator model router — routes simple messages to a cheaper/faster model.

Activated via ``model.smart_routing: true`` in config.yaml.  When active,
heuristically classifies each user message as "simple" or "complex" and
routes simple messages (greetings, acknowledgments, short questions) to the
model configured in ``auxiliary.curator`` (or the HERMES_CURATOR_MODEL env
var).  Complex messages always go to the session's primary model.

The classifier is purely heuristic — no LLM call needed.
"""

import logging
import os
import re
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_SIMPLE_PATTERNS = re.compile(
    r"^("
    r"h(i|ello|ey|owdy)|"
    r"(good )?(morning|afternoon|evening)|"
    r"thanks?( you)?|"
    r"(y(es|ep|eah)|no(pe)?|ok(ay)?|sure|right|got it|understood|"
    r"sounds good|makes sense|perfect|great|cool|nice|done|"
    r"go ahead|proceed|continue|next|lgtm|ack|nvm|never ?mind)|"
    r"what('s| is) (your|the) (name|version)|"
    r"who are you|"
    r"help|status|ping"
    r")[\s?!.]*$",
    re.IGNORECASE,
)

_CODE_BLOCK_RE = re.compile(r"```")
_FILE_PATH_RE = re.compile(r"(?:^|[\s\"'])[./~][\w/.\-]+\.[\w]+", re.MULTILINE)
_TECHNICAL_VERBS_RE = re.compile(
    r"\b(implement|refactor|debug|fix|analyze|review|optimize|design|"
    r"build|create|write|modify|update|change|add|remove|delete|"
    r"migrate|deploy|configure|set up|integrate|port|merge|rebase|"
    r"investigate|diagnose|trace|profile)\b"
    r"|"
    r"(实现|重构|调试|修复|分析|审查|优化|设计|构建|创建|编写|修改|"
    r"更新|添加|删除|迁移|部署|配置|集成|合并|诊断|排查)",
    re.IGNORECASE,
)
_CODE_SYNTAX_RE = re.compile(
    r"\b(def |class |function |import |from |const |let |var |"
    r"async |await |return |yield |raise |try:|except:|if |for |while )\b",
)

_cached_config: Optional[dict] = None
_cached_config_ts: float = 0.0
_CONFIG_TTL = 60.0


def _load_routing_config() -> dict:
    global _cached_config, _cached_config_ts
    now = time.monotonic()
    if _cached_config is not None and (now - _cached_config_ts) < _CONFIG_TTL:
        return _cached_config

    result = {
        "enabled": False,
        "model": "",
    }
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        agent_cfg = cfg.get("agent", {})
        result["enabled"] = bool(agent_cfg.get("smart_routing", False))
        aux_curator = cfg.get("auxiliary", {}).get("curator", {})
        result["model"] = aux_curator.get("model", "")
    except Exception:
        pass

    if not result["model"]:
        result["model"] = os.environ.get("HERMES_CURATOR_MODEL", "")

    _cached_config = result
    _cached_config_ts = now
    return result


def classify_complexity(message: str) -> int:
    """Score message complexity. Higher = more complex. <=0 = simple."""
    if not message or not message.strip():
        return -1

    text = message.strip()
    score = 0

    if _SIMPLE_PATTERNS.match(text):
        return -2

    length = len(text)
    if length <= 30:
        score -= 1
    elif length > 100:
        score += 1
    if length > 300:
        score += 2

    if _CODE_BLOCK_RE.search(text):
        score += 3
    if _FILE_PATH_RE.search(text):
        score += 2
    if _TECHNICAL_VERBS_RE.search(text):
        score += 2
    if _CODE_SYNTAX_RE.search(text):
        score += 1

    newlines = text.count("\n")
    if newlines > 5:
        score += 1

    return score


def route_model(
    user_message: str,
    current_model: str,
) -> Tuple[str, bool]:
    """Determine which model to use for this message.

    Returns (model_name, was_routed).  If smart routing is disabled or the
    message is complex, returns (current_model, False).
    """
    config = _load_routing_config()
    if not config["enabled"]:
        return current_model, False

    cheap_model = config["model"]
    if not cheap_model or cheap_model == current_model:
        return current_model, False

    score = classify_complexity(user_message)
    if score <= 0:
        logger.info(
            "Smart routing: simple message (score=%d), using %s instead of %s",
            score, cheap_model, current_model,
        )
        return cheap_model, True

    return current_model, False
