"""Load config.yaml and resolve paths relative to the repo root.

Why this exists: every other module asks this one for settings, so a model
name, a threshold, or a folder is changed in exactly one place.
FLUENCY.md: "model tiering".
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# The repo root is two levels above this file: src/trailheadrx/config.py
ROOT = Path(__file__).resolve().parents[2]


def _load() -> dict[str, Any]:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG: dict[str, Any] = _load()


def path(key: str) -> Path:
    """Absolute path for a key under `paths:` in config.yaml. An environment
    variable TRAILHEADRX_<KEY> (upper case) overrides it — used by tests to
    point at a temporary index and audit folder."""
    override = os.environ.get(f"TRAILHEADRX_{key.upper()}")
    if override:
        return Path(override)
    return ROOT / CONFIG["paths"][key]


def model(tier: str) -> str:
    """Model ID for a tier: 'small', 'strong', or 'embedding'."""
    return CONFIG["models"][tier]


def api_key() -> str | None:
    """The Anthropic API key, or None (which puts the app in dry-run mode)."""
    return os.environ.get("ANTHROPIC_API_KEY")


def dry_run() -> bool:
    """True when no API key is present. Every model call then returns a
    deterministic stand-in so the rest of the pipeline can be exercised."""
    return not api_key()
