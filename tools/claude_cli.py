import json
import logging
import os
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Model used for extraction prompts — haiku is fast and cheap for structured tasks
_MODEL = "claude-haiku-4-5-20251001"


def call_claude(prompt: str, timeout: int = 180) -> Optional[str]:
    """
    Call Claude via Anthropic SDK (when ANTHROPIC_API_KEY is set, e.g. GitHub Actions)
    or fall back to the local `claude` CLI subprocess (interactive Claude Code server).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return _call_via_sdk(prompt, api_key)
    return _call_via_cli(prompt, timeout)


def _call_via_sdk(prompt: str, api_key: str) -> Optional[str]:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error("Anthropic SDK call failed: %s", e)
        return None


def _call_via_cli(prompt: str, timeout: int) -> Optional[str]:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning("claude CLI exited %d: %s", result.returncode, result.stderr[:300])
            return None
        return result.stdout.strip()
    except FileNotFoundError:
        logger.error(
            "'claude' CLI not found and ANTHROPIC_API_KEY is not set. "
            "Set ANTHROPIC_API_KEY or install Claude Code: https://claude.ai/code"
        )
        return None
    except subprocess.TimeoutExpired:
        logger.error("claude CLI timed out after %ds", timeout)
        return None


def parse_json(text: str):
    """Extract a JSON value from Claude's response, handling code-fenced output."""
    if not text:
        return None
    if "```" in text:
        for block in text.split("```")[1::2]:
            cleaned = re.sub(r"^json\s*", "", block.strip())
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\[.*?\]|\{.*?\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse JSON from claude output:\n%s", text[:400])
    return None
