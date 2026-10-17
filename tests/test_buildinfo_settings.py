"""BUILD.json variants and suite.json."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.buildinfo import dump, load, parse
from voxprint_common.suite_settings import (
    file,
    gpu,
    models_dir,
    read,
    set_gpu,
    set_models_dir,
    set_theme,
    set_ui_language,
    set_window_geometry,
    theme,
    ui_language,
    window_geometry,
)


def test_canonical_round_trip() -> None:
    info = parse(
        {"app": "enlarge", "version": "0.2.0", "rc": 4, "build": 12, "codename": "North"},
    )
    assert info.label() == '0.2.0 RC 4 build 12 "North"'
    assert dump(info) == {
        "app": "enlarge",
        "version": "0.2.0",
        "rc": 4,
        "build": 12,
        "codename": "North",
    }


def test_enlarge_clarity_canvas_and_lucent() -> None:
    enlarge = parse(
        {"version": "0.2.0-rc.4", "offset": 3, "codename": "Tikvah"},
        app="enlarge",
        env={"GITHUB_RUN_NUMBER": "12"},
    )
    assert enlarge.version == "0.2.0"
    assert enlarge.rc == 4
    assert enlarge.build == 15
    assert enlarge.label() == '0.2.0 RC 4 build 15 "Tikvah"'

    clarity = parse(
        {"version": "1.0.0-rc", "build": 1002, "offset": 9, "codename": "Arukhah"},
        env={},
    )
    assert clarity.build == 1002
    assert clarity.label() == '1.0.0 RC build 1002 "Arukhah"'
    clarity_ci = parse(
        {"version": "1.0.0-rc", "build": 1002, "offset": 9, "codename": "Arukhah"},
        env={"GITHUB_RUN_NUMBER": "1"},
    )
    assert clarity_ci.build == 10

    canvas = parse(
        {
            "version": "0.1.0",
            "channel": "RC",
            "build": 1,
            "offset": 0,
            "codename": "Yotzer",
            "codename_meaning": "kept on disk, not shown",
        }
    )
    assert canvas.rc is None
    assert canvas.show_rc
    assert canvas.label() == '0.1.0 RC build 1 "Yotzer"'

    lucent = parse({"number": 7, "offset": 0, "codename": "Tov"}, app="lucent")
    assert lucent.build == 7
    assert lucent.label() == 'build 7 "Tov"'


def test_plotweaver_forge_builder_and_dubber() -> None:
    plotweaver = parse(
        {
            "version": "0.2.0",
            "prerelease": "rc5",
            "codename": "Chut",
            "offset": 4,
            "build": None,
        }
    )
    assert plotweaver.build is None
    assert plotweaver.label() == '0.2.0 RC 5 dev "Chut"'
    stamped = parse(
        {
            "version": "0.2.0",
            "prerelease": "rc5",
            "codename": "Chut",
            "offset": 4,
            "build": None,
        },
        env={"GITHUB_RUN_NUMBER": "3"},
    )
    assert stamped.build == 7

    forge = parse({"offset": 0, "codename": "Charash"}, app="forge", env={})
    assert forge.build is None
    assert forge.label() == 'dev "Charash"'

    builder = parse(
        {"offset": 944, "offset_note": "ignored", "codename": "Tamim"},
        env={"VOXPRINT_BUILD": "1001"},
    )
    assert builder.build == 1001
    dubber = parse({"offset": 983, "codename": "Kumi"}, env={"GITHUB_RUN_NUMBER": "2"})
    assert dubber.build == 985


def test_load_from_directory(tmp_path: Path) -> None:
    (tmp_path / "BUILD.json").write_text(
        json.dumps({"app": "canvas", "version": "0.1.0", "rc": None, "build": 2, "codename": ""}),
        encoding="utf-8",
    )
    info = load(tmp_path)
    assert info.label() == "0.1.0 build 2"


def test_recorded_zero_is_kept_and_missing_build_is_not_zero() -> None:
    assert parse({"build": 0, "codename": "North"}, env={}).build == 0
    assert parse({"codename": "North"}, env={}).build is None


def test_search_package_frozen_and_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    package = repo / "src" / "dubber"
    package.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'dubber'\n", encoding="utf-8")
    (repo / "BUILD.json").write_text(
        json.dumps(
            {
                "app": "dubber",
                "version": "1.2.0",
                "offset": 5,
                "codename": "Catalog",
                "build": None,
            }
        ),
        encoding="utf-8",
    )
    info = load(package_dir=package, env={"GITHUB_RUN_NUMBER": "8"})
    assert info.build == 13
    assert info.codename == "Catalog"
    assert info.version == "1.2.0"

    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "build_info.json").write_text(
        json.dumps({"build": 40, "codename": "Frozen", "version": "1.2.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(frozen / "dubber.exe"))
    packaged = load(package_dir=package, env={})
    assert packaged.build == 40
    assert packaged.codename == "Frozen"

    explicit = tmp_path / "chosen"
    explicit.mkdir()
    (explicit / "BUILD.json").write_text(
        json.dumps({"app": "dubber", "version": "1.2.0", "offset": 5, "codename": "Catalog"}),
        encoding="utf-8",
    )
    (explicit / "build_info.json").write_text(
        json.dumps({"build": 18, "codename": ""}),
        encoding="utf-8",
    )
    chosen = load(explicit, env={})
    assert chosen.build == 18
    assert chosen.codename == "Catalog"
    assert load(explicit / "BUILD.json", env={"GITHUB_RUN_NUMBER": "2"}).build == 7


def test_suite_settings_preserve_unknown_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOXPRINT_HOME", str(tmp_path))
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    assert ui_language() == "pt-BR"
    set_ui_language("en")
    set_gpu("cuda")
    set_theme("other-app")
    set_models_dir(tmp_path / "weights")
    raw = read()
    raw["custom"] = {"keep": True}
    file().write_text(json.dumps(raw), encoding="utf-8")
    set_gpu("cuda:1")
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["schema"] == 1
    assert saved["gpu"] == "cuda:1"
    assert saved["theme"] == "other-app"
    assert saved["custom"] == {"keep": True}
    assert theme() == "glass-dark"
    assert gpu() == "cuda:1"
    assert models_dir() == tmp_path / "weights"
    set_models_dir(None)
    assert models_dir() is None
    with pytest.raises(ValueError):
        set_gpu("cpu")


def test_reader_keeps_the_last_good_suite_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOXPRINT_HOME", str(tmp_path))
    assert window_geometry() is None
    set_ui_language("ru")
    set_window_geometry(1100, 800, -4, 12)
    assert file() == tmp_path / "state" / "suite.json"
    assert window_geometry() == {"width": 1100, "height": 800, "x": -4, "y": 12}
    target = file()
    target.write_text("{", encoding="utf-8")
    assert read()["ui_language"] == "ru"
    assert window_geometry() == {"width": 1100, "height": 800, "x": -4, "y": 12}
    target.write_text(json.dumps({"schema": 1, "gpu": "cuda:2"}) + "\n", encoding="utf-8")
    assert read()["gpu"] == "cuda:2"
    assert "ui_language" not in read()


def test_two_processes_keep_both_suite_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOXPRINT_HOME", str(tmp_path))
    env = os.environ.copy()
    env["VOXPRINT_HOME"] = str(tmp_path)
    root = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    child = Path(__file__).with_name("suite_settings_child.py")
    first = subprocess.Popen(
        [sys.executable, str(child), "ui.expert_mode", "true"],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        [sys.executable, str(child), "keep_awake_during_jobs", "false"],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_code = first.wait(timeout=30)
    second_code = second.wait(timeout=30)
    first_err = first.stderr.read() if first.stderr is not None else ""
    second_err = second.stderr.read() if second.stderr is not None else ""
    assert first_code == 0, first_err
    assert second_code == 0, second_err
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["ui.expert_mode"] is True
    assert saved["keep_awake_during_jobs"] is False
    assert not list(file().parent.glob(".suite.json.*.tmp"))
