"""``voxprint-mdl`` command line.

Subcommands: ``plan``, ``enqueue``, ``download``, ``status``, ``verify``, ``worker``,
``index``. ``--check`` prints which model index is in use.

``voxprint-mdl-gui --app <id>`` opens Manage models with no console. The console
script ``voxprint-mdl --gui --app <id>`` still opens that page and keeps a
console attached.

``enqueue`` appends a job and exits. ``download`` enqueues when ``--app`` or
``--manifest`` is set, then runs the single worker or attaches to the one
already running. ``status --wait`` attaches until the queue is idle.

``--json`` writes UTF-8 progress lines and one result line on stdout. Human
text goes to stderr. ``--dry-run`` builds the plan and checks free disk space
without downloading or enqueueing. Both flags are accepted before or after
the subcommand. ``--gui`` belongs on ``download`` and on ``verify``. ``verify --repair`` neutralises
unsafe manifest paths and downloads only the missing or corrupt models.
``verify --gui`` opens the check-and-repair window.

Exit codes, shared with :mod:`voxprint_common.cli_protocol`:

* ``0``: success. The result ``ok`` field is true. ``ready`` on the result,
  and in the status file, tells an app it may open.
* ``1``: usage error.
* ``2``: the operation failed. Disk space, a digest mismatch, and a network
  failure use this code. Nothing is downloaded when the disk check fails.
* ``3``: cancelled.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from voxprint_common import __version__
from voxprint_common.branding import detach_console
from voxprint_common.cli_protocol import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    Session,
    emit,
    result,
)
from voxprint_common.mdl.catalog import (
    CatalogError,
    discover,
    listed_ids,
    load_manifest,
    plan_files,
)
from voxprint_common.mdl.events import TransferSession
from voxprint_common.mdl.hooks import hook_problems
from voxprint_common.mdl.index import (
    SuiteIndexError,
    load_index,
    verify_index_file,
    write_index_signature,
)
from voxprint_common.mdl.job import Outcome, detect_tier, download, plan, verify, verify_repair
from voxprint_common.mdl.names import CLI_NAME, DISPLAY_NAME
from voxprint_common.mdl.onnx_prebuilt import stale_rows
from voxprint_common.mdl.progress_ui import run_verify_repair
from voxprint_common.mdl.queue import enqueue, load_queue, run_or_attach
from voxprint_common.mdl.readiness import publish_presence
from voxprint_common.mdl.status import JobState, StatusError, load_status
from voxprint_common.runtime_manifest import repair_manifest
from voxprint_common.ui.mdl_window import (
    WindowResult,
    run_download_window,
    run_manage_window,
    serve,
)

__all__ = ["main"]

_GLOBAL_FLAGS = frozenset({"--json", "--dry-run"})


class _Parser(argparse.ArgumentParser):
    """Argument parser that uses exit code 1 for usage errors."""

    def error(self, message: str) -> None:
        """Print the usage error and exit with code 1.

        Args:
            message: What argparse could not accept.
        """
        self.print_usage(sys.stderr)
        print(f"{CLI_NAME}: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand and return the process exit code.

    ``voxprint-mdl-gui --app <id>`` opens Manage models. ``--gui`` is implied
    for that entry point. ``voxprint-mdl --gui --app <id>`` does the same from
    the console script.

    Args:
        argv: Arguments without the program name. Defaults to ``sys.argv[1:]``.
    """
    args_list = list(sys.argv[1:] if argv is None else argv)
    if argv is None and _launched_as_gui() and "--gui" not in args_list:
        args_list = ["--gui", *args_list]
    if _bare_gui(args_list):
        return _open_named_window(args_list)
    want_json = "--json" in args_list
    parser = _parser()
    try:
        args = parser.parse_args(_globals_first(args_list))
    except SystemExit as exc:
        code = EXIT_OK if exc.code is None else exc.code
        if not isinstance(code, int):
            code = EXIT_USAGE
        if want_json and code != EXIT_OK:
            usage = code if code in {EXIT_USAGE, EXIT_FAILED, EXIT_CANCELLED} else EXIT_USAGE
            emit(result(False, code=usage, message="usage error"))
        return code
    session = Session() if args.json else None
    try:
        code, message, payload = _run(args, session)
    except (StatusError, CatalogError) as exc:
        code, message, payload = EXIT_FAILED, str(exc), {}
    if session is not None and not session.finished:
        session.finish(code == EXIT_OK, code=code, message=message, **payload)
    elif message:
        print(message, file=sys.stderr)
    return code


def _globals_first(argv: Sequence[str]) -> list[str]:
    """Move ``--json`` and ``--dry-run`` in front of the subcommand.

    Args:
        argv: Arguments without the program name.
    """
    flags = [token for token in argv if token in _GLOBAL_FLAGS]
    rest = [token for token in argv if token not in _GLOBAL_FLAGS]
    return flags + rest


def _parser() -> _Parser:
    parser = _Parser(prog=CLI_NAME, description=DISPLAY_NAME)
    parser.add_argument("--json", action="store_true", help="write JSON lines on stdout")
    parser.add_argument(
        "--dry-run", action="store_true", help="plan and check disk, do not download"
    )
    parser.add_argument("--version", action="version", version=f"{DISPLAY_NAME} {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the model index source, pin, and any fallback",
    )
    commands = parser.add_subparsers(dest="command", required=False)
    for name, help_text in (
        ("plan", "show the models this machine needs and check free disk space"),
        ("enqueue", "add a download job and exit"),
        ("download", "enqueue, then run the worker or attach to it"),
        ("status", "print the status file"),
        ("verify", "re-hash the planned models"),
        ("worker", "keep the download window and the queue worker running"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--manifest",
            action="append",
            default=[],
            help="path to an app manifest; default is to discover installed apps",
        )
        command.add_argument("--app", help="suite app id")
        command.add_argument(
            "--model",
            action="append",
            default=[],
            help="model id to include; repeat for more than one. On-demand models need this",
        )
        command.add_argument("--gui", action="store_true", help="show the progress window")
        if name == "status":
            command.add_argument(
                "--wait",
                action="store_true",
                help="run or attach until the queue is idle",
            )
        if name == "verify":
            command.add_argument(
                "--repair",
                action="store_true",
                help=(
                    "neutralise unsafe manifest paths, then download only missing or corrupt models"
                ),
            )
    index = commands.add_parser("index", help="sign or verify the model index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    sign = index_commands.add_parser("sign", help="write a detached index.json.sig")
    sign.add_argument("index_path", help="path to index.json")
    sign.add_argument("--key-file", help="Ed25519 private key; else VOXPRINT_MDL_INDEX_KEY")
    sign.add_argument("--output", help="signature path; default is index.json.sig beside the file")
    verify_index = index_commands.add_parser("verify", help="check a detached index.json.sig")
    verify_index.add_argument("index_path", help="path to index.json")
    verify_index.add_argument("--signature", help="signature path; default is index.json.sig")
    verify_index.add_argument("--public-key", help="PEM public key; default is the bundled key")
    return parser


def _run(args: argparse.Namespace, session: Session | None) -> tuple[int, str, dict[str, object]]:
    if args.check:
        if args.command:
            return EXIT_USAGE, "--check does not take a command", {}
        return _check()
    if not args.command:
        return EXIT_USAGE, "a command is required", {}
    if args.command == "index":
        return _index_command(args)
    if args.command == "worker":
        return serve(), "", {}
    if args.gui and args.command not in {"download", "verify"}:
        return EXIT_USAGE, "--gui is only valid with download or verify", {}
    if args.gui and args.command == "download":
        return _gui(args, session)
    if args.command == "status":
        if args.wait:
            return _wait(session)
        return _status(args)
    if args.command == "enqueue":
        return _enqueue(args)
    models = tuple(args.model) if args.model else None
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    on_progress = _progress(session) if session is not None else None
    if args.command == "plan":
        outcome = plan(manifests, only=models)
    elif args.command == "verify":
        if args.repair:
            notes = repair_manifest(dry_run=args.dry_run)
            if args.dry_run:
                text = "\n".join(notes) if notes else "nothing to neutralise"
                return EXIT_OK, text, {"ready": False}
        if args.gui:
            return _verify_gui(args)
        if args.repair:
            outcome = verify_repair(models, repair=True, manifests=manifests)
        else:
            outcome = verify(manifests, only=models)
    elif args.command == "download":
        if args.dry_run:
            if manifests is None and args.app:
                manifests = [app.path for app in discover() if app.id == args.app]
                if not manifests:
                    return EXIT_USAGE, f"no installed manifest for {args.app}", {}
            outcome = download(
                manifests,
                dry_run=True,
                display=False,
                on_progress=on_progress,
                only=models,
            )
        else:
            outcome = _queue_download(args, _watch(session))
    else:
        return EXIT_USAGE, f"unknown command {args.command}", {}
    return outcome.code, outcome.message, _payload(outcome.status)


def _check() -> tuple[int, str, dict[str, object]]:
    """Print the index that would be used, including a fallback reason."""
    index = load_index()
    lines = [
        f"source: {index.source}",
        f"head: {index.head}",
        f"serial: {index.serial}",
        f"generated_at: {index.generated_at}",
        f"entries: {len(index.entries)}",
        f"sha256: {index.sha256}",
    ]
    if index.fallback:
        lines.append(f"fallback: {index.fallback}")
    try:
        parsed = [load_manifest(app.path) for app in discover()]
        plan_files(parsed, detect_tier(None))
    except (CatalogError, OSError) as exc:
        lines.append(f"plan: {exc}")
    lines.extend(f"stale onnx_prebuilt: {row}" for row in stale_rows())
    lines.extend(f"not ready: {key}: {why}" for key, why in sorted(hook_problems().items()))
    return EXIT_OK, "\n".join(lines), {}


def _index_command(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Sign or verify an index file. The private key stays outside the repo."""
    try:
        match args.index_command:
            case "sign":
                path = write_index_signature(
                    Path(args.index_path),
                    key_file=Path(args.key_file) if args.key_file else None,
                    output=Path(args.output) if args.output else None,
                )
                return EXIT_OK, str(path), {}
            case "verify":
                ok = verify_index_file(
                    Path(args.index_path),
                    signature=Path(args.signature) if args.signature else None,
                    public_key_path=Path(args.public_key) if args.public_key else None,
                )
                if ok:
                    return EXIT_OK, "index signature verified", {}
                return EXIT_FAILED, "index signature is invalid", {}
            case _:
                return EXIT_USAGE, f"unknown index command {args.index_command}", {}
    except (SuiteIndexError, OSError) as exc:
        return EXIT_FAILED, str(exc), {}


def _enqueue(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    app, models, manifests = _request(args)
    if app is None:
        return EXIT_USAGE, "enqueue needs --app or --manifest", {}
    job_id = enqueue(app, models, manifests=manifests)
    jobs = [job.to_dict() for job in load_queue()]
    return EXIT_OK, job_id, {"job": job_id, "ready": False, "state": "queued", "jobs": jobs}


def _queue_download(args: argparse.Namespace, session: TransferSession | None) -> Outcome:
    app, models, manifests = _request(args)
    if app is not None:
        job_id = enqueue(app, models, manifests=manifests)
        return run_or_attach(job_id, session=session, display=args.gui)
    if not _busy():
        found = discover()
        if not found and not load_queue():
            return download(None, display=args.gui, session=session, only=models)
        known = {item.id: listed_ids(load_manifest(item.path)) for item in found}
        if models is not None and set(models).difference(*known.values()):
            return download(None, display=False, dry_run=True, only=models)
        for installed in found:
            listed = None
            if models is not None:
                listed = tuple(model for model in models if model in known[installed.id])
                if not listed:
                    continue
            enqueue(installed.id, listed)
    return run_or_attach(None, session=session, display=args.gui)


def _request(
    args: argparse.Namespace,
) -> tuple[str | None, tuple[str, ...] | None, list[Path] | None]:
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    models = tuple(args.model) if args.model else None
    app = args.app or None
    if app is None and manifests:
        app = load_manifest(manifests[0]).app
    return app, models, manifests


def _busy() -> bool:
    return any(job.state in {"queued", "running"} for job in load_queue())


def _watch(session: Session | None) -> TransferSession | None:
    if session is None:
        return None
    transfer = TransferSession()

    def on_event(event: object) -> None:
        fraction = getattr(event, "bytes_done", 0)
        total = getattr(event, "bytes_total", 0)
        ratio = None if not total else min(1.0, fraction / total)
        session.progress("download", ratio, str(getattr(event, "state", "")))

    transfer.subscribe(on_event)
    return transfer


def _wait(session: Session | None) -> tuple[int, str, dict[str, object]]:
    outcome = run_or_attach(None, session=_watch(session))
    return outcome.code, outcome.message, _payload(outcome.status)


def _launched_as_gui() -> bool:
    """True when this process is the windowed ``voxprint-mdl-gui`` entry point."""
    stem = Path(sys.argv[0]).name.lower().removesuffix(".exe")
    return stem == "voxprint-mdl-gui"


def _bare_gui(argv: Sequence[str]) -> bool:
    commands = {"plan", "enqueue", "download", "status", "verify", "worker"}
    return "--gui" in argv and not any(token in commands for token in argv)


def _open_named_window(argv: Sequence[str]) -> int:
    detach_console()
    tokens = list(argv)
    app = _flag_value(tokens, "--app")
    manifest = _flag_value(tokens, "--manifest")
    if not app:
        return EXIT_USAGE
    try:
        outcome = run_manage_window(app, manifest=manifest or None)
    except ImportError as exc:
        print(f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", file=sys.stderr)
        return EXIT_FAILED
    return _window_code(outcome)


def _flag_value(tokens: Sequence[str], flag: str) -> str:
    if flag in tokens:
        index = tokens.index(flag)
        if index + 1 < len(tokens):
            return tokens[index + 1]
    return ""


def _window_code(outcome: WindowResult) -> int:
    match outcome:
        case WindowResult.READY:
            return EXIT_OK
        case WindowResult.CANCELLED:
            return EXIT_CANCELLED
        case WindowResult.FAILED:
            return EXIT_FAILED
        case _ as unreachable:
            raise AssertionError(unreachable)


def _verify_gui(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Open the check-and-repair window for ``voxprint-mdl verify --gui``."""
    detach_console()
    models = tuple(args.model) if args.model else None
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    try:
        outcome = run_verify_repair(
            models,
            repair=bool(args.repair),
            manifests=manifests,
            show=True,
        )
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}
    return outcome.code, outcome.message, _payload(outcome.status)


def _gui(args: argparse.Namespace, session: Session | None) -> tuple[int, str, dict[str, object]]:
    del session
    detach_console()
    app = args.app or "suite"
    manifests = [Path(item) for item in args.manifest]
    try:
        outcome = run_download_window(
            app,
            manifest=manifests[0] if manifests else None,
            manifests=manifests or None,
        )
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}
    status = load_status()
    message = status.error or ("ready" if status.ready else status.state.value)
    return _window_code(outcome), message, _payload(status)


def _status(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    status = publish_presence(args.app)
    message = status.error or ("ready" if status.ready else status.state.value)
    return EXIT_OK, message, _payload(status)


def _payload(status: object) -> dict[str, object]:
    ready = bool(getattr(status, "ready", False))
    state = getattr(status, "state", JobState.IDLE)
    value = state.value if isinstance(state, JobState) else str(state)
    jobs = getattr(status, "jobs", [])
    return {"ready": ready, "state": value, "jobs": list(jobs or [])}


def _progress(session: Session):
    def on_progress(fraction: float | None, message: str) -> None:
        session.progress("download", fraction, message)

    return on_progress
