"""Post-install commands declared by an app manifest.

The worker runs a hook after the file digest matches and the bytes are in
place. The executable is taken from the app install directory, never from
``PATH``. ``{id}``, ``{model_dir}``, and ``{models_root}`` are filled in.
A hook is skipped when every ``produces`` path exists and the marker records
the source digest. Verify and repair delete that assumption by passing the
current digest, so a changed file runs the hook again.

Failure sets the item to ``error`` with a short message. The process stderr
is appended to the hook log and is not shown in the window.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.descriptor import PostInstall
from voxprint_common.paths import models_dir, state_dir

__all__ = ["HookError", "HookPlan", "apply_hooks", "files_awaiting_hooks"]


class HookError(Exception):
    """A post-install command failed. The message is safe to show."""

    def __init__(self, message: str, *, app: str, model_id: str) -> None:
        """Store the friendly text and which item failed.

        Args:
            message: Text for the status file and the window.
            app: Suite app id.
            model_id: Model id, or empty for an app-wide hook.
        """
        super().__init__(message)
        self.app = app
        self.model_id = model_id


@dataclass(frozen=True)
class HookPlan:
    """One hook bound to the app install directory."""

    app: str
    install_dir: Path
    hook: PostInstall


def apply_hooks(files: Sequence[object]) -> None:
    """Run each planned hook once.

    Args:
        files: Planned files that already passed the digest check. Each file
            carries the hooks that apply to it.

    Raises:
        HookError: The command failed, timed out, or its executable is missing.
    """
    for plan, related in _groups(files):
        _run(plan, related)


def files_awaiting_hooks(files: Sequence[object]) -> set[int]:
    """Return ``id(file)`` for files whose hooks still have to run.

    Args:
        files: Planned files. The caller must keep these objects: the result
            is their identities, not a copy.

    A file is included when a hook that applies to it is missing its marker
    or one of its ``produces`` paths.
    """
    waiting: set[int] = set()
    for plan, related in _groups(files):
        folder = models_dir() / plan.app / (plan.hook.model_id or "_app")
        if _satisfied(plan, folder, _source_sha(related)):
            continue
        for item in related:
            waiting.add(id(item))
    return waiting


def _groups(files: Sequence[object]) -> list[tuple[HookPlan, list[object]]]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    groups: list[tuple[HookPlan, list[object]]] = []
    for item in files:
        for plan in getattr(item, "hooks", ()):
            scope = plan.hook.model_id
            model_id = getattr(item, "model_id", "")
            model_ids = getattr(item, "model_ids", ())
            if scope and scope != model_id and scope not in model_ids:
                continue
            key = (plan.app, scope, plan.hook.argv)
            if key in seen:
                continue
            seen.add(key)
            related = [
                other
                for other in files
                if plan.app in getattr(other, "apps", ())
                and (
                    not scope
                    or scope == getattr(other, "model_id", "")
                    or scope in getattr(other, "model_ids", ())
                )
            ]
            groups.append((plan, related or [item]))
    return groups


def _run(plan: HookPlan, files: list[object]) -> None:
    identity = plan.hook.model_id or plan.app
    folder = models_dir() / plan.app / (plan.hook.model_id or "_app")
    folder.mkdir(parents=True, exist_ok=True)
    digest = _source_sha(files)
    if _satisfied(plan, folder, digest):
        return
    executable = _resolve(plan.hook.argv[0], plan.install_dir, plan.app, identity)
    argv = [str(executable)]
    for token in plan.hook.argv[1:]:
        argv.append(_expand(token, model_id=identity, model_dir=folder, models_root=models_dir()))
    try:
        completed = subprocess.run(
            argv,
            cwd=plan.install_dir,
            capture_output=True,
            timeout=plan.hook.timeout_s,
            check=False,
            **_windows_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        _log(plan.app, identity, -1, exc.stderr)
        raise HookError(
            f"Preparing {identity} took too long.",
            app=plan.app,
            model_id=identity,
        ) from exc
    except OSError as exc:
        _log(plan.app, identity, -1, str(exc).encode())
        raise HookError(
            f"Could not finish preparing {identity}.",
            app=plan.app,
            model_id=identity,
        ) from exc
    _log(plan.app, identity, completed.returncode, completed.stderr)
    if completed.returncode != 0:
        raise HookError(f"Could not finish preparing {identity}.", app=plan.app, model_id=identity)
    if not _produces_ready(plan, folder):
        raise HookError(
            f"Could not finish preparing {identity}.",
            app=plan.app,
            model_id=identity,
        )
    _marker(plan, digest).write_text(digest + "\n", encoding="utf-8")


def _satisfied(plan: HookPlan, folder: Path, digest: str) -> bool:
    marker = _marker(plan, digest)
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return recorded == digest and _produces_ready(plan, folder)


def _produces_ready(plan: HookPlan, folder: Path) -> bool:
    if not plan.hook.produces:
        return True
    return all(_produced(folder, relative).is_file() for relative in plan.hook.produces)


def _produced(folder: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise HookError(
            "A post-install path must stay inside the model folder.",
            app="",
            model_id="",
        )
    return folder / path


def _marker(plan: HookPlan, digest: str) -> Path:
    scope = plan.hook.model_id or "_app"
    folder = state_dir() / "mdl-hooks" / plan.app / scope
    folder.mkdir(parents=True, exist_ok=True)
    return folder / digest


def _source_sha(files: list[object]) -> str:
    rows = sorted(f"{getattr(item, 'name', '')}:{getattr(item, 'sha256', '')}" for item in files)
    if len(rows) == 1:
        return str(getattr(files[0], "sha256", ""))
    return sha256_bytes("\n".join(rows).encode("utf-8"))


def _resolve(token: str, install_dir: Path, app: str, model_id: str) -> Path:
    name = Path(token).name
    candidates = [install_dir / name, install_dir / "bin" / name]
    if os.name == "nt":
        candidates.extend([install_dir / f"{name}.exe", install_dir / "bin" / f"{name}.exe"])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise HookError(
        f"Could not find {name} in the {app} install folder.",
        app=app,
        model_id=model_id,
    )


def _expand(token: str, *, model_id: str, model_dir: Path, models_root: Path) -> str:
    return (
        token.replace("{id}", model_id)
        .replace("{model_dir}", str(model_dir))
        .replace("{models_root}", str(models_root))
    )


def _log(app: str, model_id: str, code: int, stderr: bytes | None) -> None:
    path = state_dir() / "mdl-hook.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (stderr or b"").decode("utf-8", errors="replace")
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {app} {model_id} exit {code}\n{text}")


def _windows_flags(platform: str | None = None) -> dict[str, int]:
    if (os.name if platform is None else platform) != "nt":
        return {}
    flag = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not flag:
        return {}
    return {"creationflags": flag}
