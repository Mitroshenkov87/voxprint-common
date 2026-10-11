"""CLI lines, self-test documents, palette, splash layout, and the provenance stub."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from voxprint_common import provenance
from voxprint_common.buildinfo import parse
from voxprint_common.cli_protocol import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    ProtocolError,
    Session,
    iter_lines,
)
from voxprint_common.selftest import Check, check_call, document, write
from voxprint_common.ui import splash, theme


def test_json_lines_and_exit_codes() -> None:
    buffer = io.StringIO()
    session = Session(buffer)
    session.progress("download", None, "starting")
    session.progress("download", 0.5, "mirror 2")
    code = session.finish(True, message="done", path="out.bin")
    assert code == EXIT_OK
    rows = list(iter_lines(io.StringIO(buffer.getvalue())))
    assert [row["type"] for row in rows] == ["progress", "progress", "result"]
    assert rows[-1]["ok"] is True
    assert rows[-1]["path"] == "out.bin"
    with pytest.raises(ProtocolError):
        session.progress("download", 1, "late")
    failed = Session(io.StringIO())
    assert failed.finish(False, code=EXIT_USAGE, message="missing input") == EXIT_USAGE
    assert Session(io.StringIO()).finish(False, code=EXIT_FAILED) == EXIT_FAILED
    assert Session(io.StringIO()).finish(False, code=EXIT_CANCELLED) == EXIT_CANCELLED
    with pytest.raises(ValueError):
        Session(io.StringIO()).finish(True, code=EXIT_FAILED)


def test_selftest_document(tmp_path: Path) -> None:
    checks = [
        check_call("paths", lambda: None),
        check_call("gpu", lambda: (_ for _ in ()).throw(RuntimeError("no GPU"))),
    ]
    assert checks[1].detail == "no GPU"
    body = document(checks)
    assert body["ok"] is False
    assert document([])["ok"] is False
    target = tmp_path / "checks.json"
    write(target, [Check("paths", True, "")])
    assert '"ok": true' in target.read_text(encoding="utf-8")


def test_palette_contrast_and_stylesheet() -> None:
    assert theme.PRIMARY == "#f5d76e"
    assert theme.ACCENT == "#60a5fa"
    assert theme.TOKENS["RED"] == "#fca5a5"
    for foreground, background in theme.CONTRAST_PAIRS:
        ratio = theme.contrast_ratio(foreground, background)
        assert ratio >= 4.5, f"{foreground} on {background} is {ratio:.2f}"
    plain = theme.build_style(False)
    glass = theme.build_style(True, more=True)
    assert theme.ROOT_PLAIN in plain
    assert theme.ROOT_GLASS_MORE in glass
    assert theme.PRIMARY in plain
    assert "QPushButton#gear" in plain
    assert "QPushButton#disclosure" in plain
    assert "QPushButton#disclosure:hover" in plain
    assert "PySide6" not in sys.modules


def test_splash_layout_without_qt() -> None:
    boxes = splash.layout()
    assert (boxes.width, boxes.height, boxes.radius) == (960, 540, 12)
    assert boxes.left_third == 320
    assert boxes.text_x == 48
    assert boxes.text_width == 320 - 48
    assert boxes.bar_width == boxes.text_width
    assert boxes.bar_height == 4
    assert boxes.bar_y == 540 - 48 - 4
    assert splash.MIN_VISIBLE_S == 0.6
    assert splash.FADE_MS == 200
    assert splash.display_name("enlarge") == "Voxprint AI Enlarge"
    assert splash.display_name("Voxprint AI Canvas") == "Voxprint AI Canvas"
    assert splash.parse_colour("#ff6b8a") == (255, 107, 138)
    seen: list[splash.SplashEvent] = []
    window = splash.Splash.__new__(splash.Splash)
    window.on_click = seen.append
    click = splash.SplashEvent(12, 34, "left")
    window.mouse_press(click)
    assert seen == [click]
    info = parse({"app": "enlarge", "version": "0.2.0", "rc": 4, "build": 12, "codename": "North"})
    assert info.label() == '0.2.0 RC 4 build 12 "North"'
    assert "PySide6" not in sys.modules


def test_provenance_is_a_stub() -> None:
    settings = provenance.ProvenanceSettings()
    assert settings.watermark is True
    assert settings.label is False
    ctx = provenance.ProvenanceContext(
        app="canvas",
        version="0.1.0",
        build=12,
        source_type="trainedAlgorithmicMedia",
        action="c2pa.created",
    )
    with pytest.raises(provenance.ProvenanceUnavailable):
        provenance.embed_image(object(), ctx, settings)
    with pytest.raises(provenance.ProvenanceUnavailable):
        provenance.finalize(Path("out.avif"), ctx, settings)
    with pytest.raises(provenance.ProvenanceUnavailable):
        provenance.detect("out.avif")
    assert "human-made" in provenance.NO_MARK_NOTE
