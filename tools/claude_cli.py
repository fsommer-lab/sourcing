import json
import logging
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def call_claude(prompt: str, timeout: int = 180) -> Optional[str]:
    """
    Run the local `claude` CLI in non-interactive mode and return its output.
    Uses the Claude Code installation already on this machine — no API key needed.
    """
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
            "'claude' command not found. "
            "Make sure Claude Code is installed and in your PATH: https://claude.ai/code"
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
