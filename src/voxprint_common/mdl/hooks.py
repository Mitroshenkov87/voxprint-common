"""Post-install commands declared by an app manifest.

The worker runs a hook after the file digest matches and the bytes are in
place. The executable is taken from the app install directory, never from
``PATH``, and must resolve inside it. ``{id}``, ``{model_dir}``,
``{source_path}``, ``{prebuilt_path}``, and ``{cache_dir}`` are filled in
(``{models_root}`` only for the older ``argv`` form). The process runs
without a shell, with ``VOXPRINT_OFFLINE=1`` and every proxy variable set
to ``127.0.0.1:9``. A timeout kills the process tree. Stdout and stderr go
to the hook log, keeping the last 64 KB. Each declared output must stay in
the model folder and match its sha256 when one is given.
A hook is skipped when every ``produces`` path exists and the marker records
the source digest. Verify and repair delete that assumption by passing the
current digest, so a changed file runs the hook again.

Failure sets the item to ``error`` with a short message. The process stderr
is appended to the hook log and is not shown in the window.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from voxprint_common.hashing import sha256_bytes, sha256_file
from voxprint_common.mdl.descriptor import PostInstall
from voxprint_common.paths import cache_dir, models_dir, state_dir

__all__ = [
    "HookError",
    "HookPlan",
    "apply_hooks",
    "files_awaiting_hooks",
    "hook_problems",
    "remove_outputs",
]

LOG_TAIL_BYTES = 64 * 1024
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


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
    prebuilt_path: str = ""


def apply_hooks(files: Sequence[object], *, deep: bool = False) -> None:
    """Run each planned hook once.

    Args:
        files: Planned files that already passed the digest check. Each file
            carries the hooks that apply to it.
        deep: Re-hash outputs that declare a sha256, as verify does.

    Raises:
        HookError: The command failed, timed out, its executable is missing,
            or an output is missing or does not match.
    """
    for plan, related in _groups(files):
        identity = plan.hook.model_id or plan.app
        try:
            _run(plan, related, deep=deep)
        except HookError as exc:
            _note_problem(plan.app, identity, str(exc))
            raise
        _note_problem(plan.app, identity, "")


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


def _run(plan: HookPlan, files: list[object], *, deep: bool = False) -> None:
    identity = plan.hook.model_id or plan.app
    folder = models_dir() / plan.app / (plan.hook.model_id or "_app")
    folder.mkdir(parents=True, exist_ok=True)
    digest = _source_sha(files)
    if _satisfied(plan, folder, digest, deep=deep):
        return
    executable = _resolve(plan.hook.cli or plan.hook.argv[0], plan.install_dir, plan.app, identity)
    source = next((str(getattr(item, "destination", "")) for item in files), "")
    values = {
        "id": identity,
        "model_dir": str(folder),
        "source_path": source,
        "prebuilt_path": plan.prebuilt_path,
        "cache_dir": str(cache_dir()),
    }
    if not plan.hook.cli:
        values["models_root"] = str(models_dir())
    argv = [str(executable)]
    argv.extend(_expand(token, values) for token in plan.hook.argv[1:])
    failed = HookError(f"Could not finish preparing {identity}.", app=plan.app, model_id=identity)
    try:
        code, output = _spawn(argv, plan.install_dir, plan.hook.timeout_s)
    except subprocess.TimeoutExpired as exc:
        _log(plan.app, identity, -1, exc.output if isinstance(exc.output, bytes) else b"")
        raise HookError(
            f"Preparing {identity} took too long.",
            app=plan.app,
            model_id=identity,
        ) from exc
    except OSError as exc:
        _log(plan.app, identity, -1, str(exc).encode())
        raise failed from exc
    _log(plan.app, identity, code, output)
    if code != 0:
        raise failed
    if not _produces_ready(plan, folder):
        raise failed
    for relative, expected in zip(plan.hook.produces, plan.hook.output_sha256, strict=False):
        if expected and sha256_file(_produced(folder, relative)) != expected:
            raise HookError(
                f"A prepared file for {identity} did not match its checksum.",
                app=plan.app,
                model_id=identity,
            )
    _marker(plan, digest).write_text(_marker_text(plan, folder, digest), encoding="utf-8")


def _offline_env() -> dict[str, str]:
    env = dict(os.environ)
    env["VOXPRINT_OFFLINE"] = "1"
    for name in _PROXY_VARS:
        env[name] = "http://127.0.0.1:9"
    env.pop("NO_PROXY", None)
    env.pop("no_proxy", None)
    return env


def _spawn(argv: list[str], cwd: Path, timeout: float) -> tuple[int, bytes]:
    """Run without a shell, kill the whole tree on timeout, and return stdout plus stderr."""
    flags = _spawn_flags()
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_offline_env(),
        shell=False,
        **flags,  # type: ignore[arg-type]
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(process)
        output, _ = process.communicate()
        raise subprocess.TimeoutExpired(argv, timeout, output=output) from exc
    return process.returncode, output or b""


def _spawn_flags(platform: str | None = None) -> dict[str, object]:
    """Return Popen flags that start a new process group without a console window."""
    if (os.name if platform is None else platform) != "nt":
        return {"start_new_session": True}
    group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    return {"creationflags": group | _windows_flags(platform).get("creationflags", 0)}


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
            **_windows_flags(),
        )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    process.kill()


def _marker_text(plan: HookPlan, folder: Path, digest: str) -> str:
    rows = [digest]
    for relative in plan.hook.produces:
        info = _produced(folder, relative).stat()
        rows.append(json.dumps([relative, info.st_size, info.st_mtime_ns]))
    return "\n".join(rows) + "\n"


def _satisfied(plan: HookPlan, folder: Path, digest: str, *, deep: bool = False) -> bool:
    marker = _marker(plan, digest)
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    if not lines or lines[0].strip() != digest or not _produces_ready(plan, folder):
        return False
    recorded = {}
    for line in lines[1:]:
        try:
            relative, size, mtime = json.loads(line)
        except ValueError:
            return False
        recorded[relative] = (size, mtime)
    for relative, expected in zip(
        plan.hook.produces,
        plan.hook.output_sha256 or ("",) * len(plan.hook.produces),
        strict=False,
    ):
        info = _produced(folder, relative).stat()
        if relative in recorded and recorded[relative] != (info.st_size, info.st_mtime_ns):
            return False
        if deep and expected and sha256_file(_produced(folder, relative)) != expected:
            return False
    return True


def _produces_ready(plan: HookPlan, folder: Path) -> bool:
    if not plan.hook.produces:
        return True
    return all(_produced(folder, relative).is_file() for relative in plan.hook.produces)


def _produced(folder: Path, relative: str) -> Path:
    path = Path(relative)
    target = (folder / path).resolve()
    if path.is_absolute() or ".." in path.parts or not target.is_relative_to(folder.resolve()):
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
    base = install_dir.resolve()
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve().is_relative_to(base):
            return candidate
    raise HookError(
        f"Could not find {name} in the {app} install folder.",
        app=app,
        model_id=model_id,
    )


def _expand(token: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        token = token.replace("{" + name + "}", value)
    return token


def _problems_path() -> Path:
    return state_dir() / "mdl-hooks" / "problems.json"


def _note_problem(app: str, model_id: str, reason: str) -> None:
    path = _problems_path()
    rows = hook_problems()
    key = f"{app}/{model_id}"
    if reason:
        rows[key] = reason
    elif key in rows:
        del rows[key]
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def hook_problems() -> dict[str, str]:
    """Return ``{"app/model": reason}`` for post-install commands that last failed.

    The model stays not ready, and the next install runs the command again.
    """
    try:
        payload = json.loads(_problems_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def remove_outputs(app: str, component_id: str) -> None:
    """Delete post-install outputs and markers for a model that was removed.

    Args:
        app: Suite app id.
        component_id: ``model-<sha prefix>`` of the removed model.
    """
    prefix = component_id.removeprefix("model-")
    root = state_dir() / "mdl-hooks" / app
    if not prefix or not root.is_dir():
        return
    for marker in root.glob(f"*/{prefix}*"):
        folder = models_dir() / app / marker.parent.name
        for line in marker.read_text(encoding="utf-8").splitlines()[1:]:
            try:
                relative = json.loads(line)[0]
                _produced(folder, relative).unlink(missing_ok=True)
            except ValueError, HookError, OSError, IndexError:
                continue
        marker.unlink(missing_ok=True)


def _log(app: str, model_id: str, code: int, stderr: bytes | None) -> None:
    path = state_dir() / "mdl-hook.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = stderr or b""
    if len(data) > LOG_TAIL_BYTES:
        data = b"[...]\n" + data[-LOG_TAIL_BYTES:]
    text = data.decode("utf-8", errors="replace")
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
