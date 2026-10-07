"""Lint-style checks of the Docker image definition (no Docker needed)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKER = REPO / "packaging" / "docker"
SERVICE = DOCKER / "rootfs" / "etc" / "services.d" / "headless"


def _dockerfile() -> str:
    return (DOCKER / "Dockerfile").read_text(encoding="utf-8")


def _env() -> dict:
    """KEY=VALUE pairs of the Dockerfile's ENV instructions (comments and continuations removed)."""
    lines = [ln for ln in _dockerfile().splitlines() if not ln.strip().startswith("#")]
    text = "\n".join(lines).replace("\\\n", " ")
    env = {}
    for m in re.finditer(r"^ENV\s+(.+)$", text, flags=re.M):
        for k, v in re.findall(r"(\w+)=(\S+)", m.group(1)):
            env[k] = v
    return env


def test_base_image_is_a_pinned_debian_jlesage_gui():
    m = re.search(r"^ARG BASEIMAGE=(\S+)$", _dockerfile(), flags=re.M)
    assert m, "base image set through ARG BASEIMAGE"
    assert re.fullmatch(r"jlesage/baseimage-gui:debian-\d+-v\d+\.\d+\.\d+", m.group(1)), \
        "glibc (Debian) base for the PySide6 wheels, pinned to an exact version"
    assert "FROM ${BASEIMAGE}" in _dockerfile()


def test_unraid_and_clipboard_defaults():
    env = _env()
    assert env["USER_ID"] == "99" and env["GROUP_ID"] == "100"
    assert env["SECURE_CONNECTION"] == "1", "clipboard sync needs HTTPS"
    assert env["WEB_HOST_CLIPBOARD_SYNC"] == "1"
    assert env["MANGALIST_DATA_DIR"] == "/config"
    assert env["MANGALIST_HEADLESS"] == "1"
    assert env["MANGALIST_DOWNLOADS"] == "0", "downloads are opt-in"


def test_env_defaults_match_the_runner_defaults():
    from mangalist import paths
    from mangalist.headless import settings

    env = _env()
    assert paths.ENV_DATA_DIR in env
    assert env[settings.ENV_RESCAN] == settings.DEFAULT_RESCAN
    assert env[settings.ENV_DISPATCH] == settings.DEFAULT_DISPATCH
    assert settings.parse_schedule(env[settings.ENV_DOWNLOADS_SCHEDULE]) == \
        settings.parse_schedule(settings.DEFAULT_DOWNLOADS_SCHEDULE)
    s = settings.HeadlessSettings.from_env(env)
    assert s.downloads_enabled is False and s.rescan_schedule is not None


def test_volumes_port_and_name():
    df = _dockerfile()
    assert 'VOLUME ["/config", "/data"]' in df
    assert re.search(r"^EXPOSE 5800$", df, flags=re.M)
    assert 'set-cont-env APP_NAME "MangaList"' in df
    assert 'org.opencontainers.image.title="MangaList"' in df


def test_startapp_and_service_scripts():
    start = (DOCKER / "startapp.sh").read_text(encoding="utf-8")
    assert start.startswith("#!/bin/sh") and "exec /opt/mangalist/venv/bin/python -m mangalist" in start
    run = (SERVICE / "run").read_text(encoding="utf-8")
    assert run.startswith("#!/bin/sh") and "-m mangalist --headless" in run
    assert "trap " in run, "SIGTERM is forwarded to the runner"
    disabled = (SERVICE / "disabled").read_text(encoding="utf-8")
    assert "MANGALIST_HEADLESS" in disabled
    assert (SERVICE / "respawn").exists()
    assert (DOCKER / "rootfs" / "etc" / "services.d" / "default" / "headless.dep").exists()
    for f in (DOCKER / "startapp.sh", SERVICE / "run", SERVICE / "disabled"):
        assert b"\r\n" not in f.read_bytes(), f"{f.name}: LF line endings"


def test_scripts_are_executable_in_git():
    try:
        out = subprocess.run(["git", "ls-files", "-s", "packaging/docker"], cwd=str(REPO),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("not a git checkout")
    modes = {line.split("\t")[1]: line.split()[0] for line in out.stdout.splitlines()}
    for name in ("startapp.sh", "rootfs/etc/services.d/headless/run",
                 "rootfs/etc/services.d/headless/disabled"):
        assert modes.get(f"packaging/docker/{name}") == "100755", name


def test_dockerignore_keeps_the_context_small():
    lines = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**" in lines
    assert {"!requirements.txt", "!mangalist/", "!packaging/docker/"} <= set(lines)
