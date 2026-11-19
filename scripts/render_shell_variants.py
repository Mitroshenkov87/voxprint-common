"""Paint one sample shared-shell window per app type, in English.

Run with ``QT_QPA_PLATFORM=offscreen``. The output directory is argv[1] and
is required; the PNGs are written there and nowhere else. A child process
paints the same windows at 150% (``QT_SCALE_FACTOR``). Suite state is
redirected to a temp dir so the user's real State dir is untouched. Nothing
is downloaded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

if os.environ.get("SHELL_RENDER_SCALE") == "150":
    os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_STATE_HOME = Path(tempfile.mkdtemp(prefix="voxprint-shell-render-"))
os.environ["VOXPRINT_HOME"] = str(_STATE_HOME)

from voxprint_common.i18n import set_language  # noqa: E402
from voxprint_common.suite_settings import set_expert_mode  # noqa: E402
from voxprint_common.ui.shell import ReferenceShell  # noqa: E402

_CASES = (
    ("image", "canvas", "A quiet hill at dusk", True),
    ("audio", "audiobook-builder", "The quiet chapter book", False),
    ("video", "clarity", "The quiet film", False),
    ("text", "plotweaver", "A hill at dusk, retold", False),
)


def _paint(name: str, app_id: str, prompt: str, result: bool, out: Path) -> Path:
    set_language("en")
    set_expert_mode(False)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    scaled = os.environ.get("SHELL_RENDER_SCALE") == "150"
    path = out / f"{name}{'-150' if scaled else '-100'}.png"
    print(f"painting {path.name}", flush=True)
    shell = ReferenceShell(app=app_id, state_dir=_STATE_HOME / "state" / name)
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()
    if result:
        shell.show_result(prompt, pictures=4)
    else:
        shell.simple.set_prompt_text(prompt)
        shell.simple.refresh(prompt=True)
    app.processEvents()
    shell.grab().save(str(path))
    shell.close()
    app.processEvents()
    print(f"  {path.name} bytes={path.stat().st_size}", flush=True)
    return path


def main() -> None:
    """Write the 100% renders, then the 150% renders in a child process."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: render_shell_variants.py OUTPUT_DIR")
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for name, app_id, prompt, result in _CASES:
        try:
            _paint(name, app_id, prompt, result, out)
        except Exception as exc:  # skip one bad variant, record it below
            failures.append(f"{name}: {exc!r}")
            print(f"  {name} skipped: {exc!r}", flush=True)
    if os.environ.get("SHELL_RENDER_SCALE") != "150":
        env = os.environ.copy()
        env["SHELL_RENDER_SCALE"] = "150"
        env["QT_SCALE_FACTOR"] = "1.5"
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            child = [sys.executable, str(Path(__file__).resolve()), str(out)]
            subprocess.check_call(child, env=env)
        except subprocess.CalledProcessError as exc:
            failures.append(f"150% pass: exit {exc.returncode}")
    if failures:
        lines = "Some renders were skipped:\n" + "\n".join(failures) + "\n"
        (out / "renders-readme.txt").write_text(lines, encoding="utf-8")
        print(lines, flush=True)


if __name__ == "__main__":
    main()
