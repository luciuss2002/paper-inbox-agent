"""Configuration loaders for runtime / sources / research_profile."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

CONFIGS_DIR = Path("configs")
DEFAULT_RUNTIME_PATH = CONFIGS_DIR / "runtime.yaml"
DEFAULT_SOURCES_PATH = CONFIGS_DIR / "sources.yaml"
DEFAULT_PROFILE_PATH = CONFIGS_DIR / "research_profile.yaml"


@dataclass(frozen=True)
class Settings:
    runtime: dict[str, Any]
    sources: dict[str, Any]
    profile: dict[str, Any]
    runtime_path: Path
    sources_path: Path
    profile_path: Path


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {p}")
    return data


def load_settings(
    runtime_path: str | Path | None = None,
    sources_path: str | Path | None = None,
    profile_path: str | Path | None = None,
) -> Settings:
    """Load env + the three YAML config files."""
    load_dotenv(override=False)

    rp = Path(runtime_path or DEFAULT_RUNTIME_PATH)
    sp = Path(sources_path or DEFAULT_SOURCES_PATH)
    pp = Path(profile_path or DEFAULT_PROFILE_PATH)

    return Settings(
        runtime=load_yaml(rp),
        sources=load_yaml(sp),
        profile=load_yaml(pp),
        runtime_path=rp,
        sources_path=sp,
        profile_path=pp,
    )


def copy_example_if_missing(target: Path, example: Path) -> bool:
    """Copy `example` to `target` if `target` does not yet exist. Return True if copied."""
    if target.exists():
        return False
    if not example.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(example, target)
    return True


def init_default_configs() -> list[Path]:
    """Copy *.example.yaml -> *.yaml for any missing main config. Return list of created paths."""
    created: list[Path] = []
    pairs = [
        (DEFAULT_RUNTIME_PATH, CONFIGS_DIR / "runtime.example.yaml"),
        (DEFAULT_SOURCES_PATH, CONFIGS_DIR / "sources.example.yaml"),
        (DEFAULT_PROFILE_PATH, CONFIGS_DIR / "research_profile.example.yaml"),
    ]
    for target, example in pairs:
        if copy_example_if_missing(target, example):
            created.append(target)
    return created


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)
