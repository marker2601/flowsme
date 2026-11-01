"""Shared fixtures. Nothing here calls an AI model or the web: the keys are removed from the environment and every
config is offline, so the tests cost nothing and run without internet."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from flowsme.config import Config

for _key in ("MODEL_API_KEY", "LLM_API_KEY"):
    os.environ.pop(_key, None)


def offline_config(sessions_dir: Path) -> Config:
    cfg = Config()
    cfg.sessions_dir = sessions_dir
    cfg.llm.provider = "offline"
    cfg.analysis.web_search = False
    return cfg


@pytest.fixture(scope="session")
def analysed_demo(tmp_path_factory) -> tuple[Path, dict]:
    """The synthetic invoice-processing recording, analysed once for the whole test run."""
    from flowsme.analyze import analyze_session
    from flowsme.demo import build_demo

    sessions = tmp_path_factory.mktemp("sessions")
    path = build_demo(sessions)
    result = analyze_session(path, offline_config(sessions), log=lambda *_: None, hours_copy=True)
    return path, result

