"""BUILD.json variants and suite.json."""

from __future__ import annotations

import json
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
    theme,
    ui_language,
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
        env={"GITHUB_RUN_NUMBER": "1"},
    )
    assert clarity.build == 1002
    assert clarity.label() == '1.0.0 RC build 1002 "Arukhah"'

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

    forge = parse({"offset": 0, "codename": "Charash"}, app="forge")
    assert forge.build == 0
    assert forge.label() == 'build 0 "Charash"'

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
