"""``voxprint-mdl`` command line.

Subcommands: ``plan``, ``enqueue``, ``download``, ``ensure``, ``adopt``,
``repair-store``, ``cleanup``, ``status``, ``verify``, ``worker``,
``index``, ``open-tab``. ``--check`` prints which model index is in use.

``voxprint-mdl-gui`` opens Manage models with no console. With no ``--app``
it uses the first installed app, or the first suite app if none is
installed. ``voxprint-mdl --gui --app <id>`` still opens that page and keeps
a console attached.

``enqueue`` appends a job and exits. ``download`` enqueues when ``--app`` or
``--manifest`` is set, then runs the single worker or attaches to the one
already running. ``ensure`` is the headless installer entry point: it reads
the app id from the manifest, enqueues that app's planned models, and runs
or attaches to the single worker. It never opens a window and never prompts.
``status --wait`` attaches until the queue is idle.

``repair-store`` adopts complete copies of the planned files that are
already on disk (another app's folder such as ``model-forge``, a view, or
a ``--from`` directory) into the shared blob store, then rebuilds stub or
missing app views. With no ``--manifest``/``--app-manifest``/``--app`` it
discovers and repairs every installed app — the one-time laptop
migration. It never downloads, never deletes blobs, and never touches an
app folder. Exit code 0 means everything is ready, 2 means a file or view
is still missing, so an installer can fall back to ``ensure``.

``--app-manifest PATH`` is an alias of ``--manifest`` on every subcommand
that takes a manifest. ``--json`` writes UTF-8 progress lines and one result
line on stdout. Human text goes to stderr. For ``ensure`` and ``verify`` the
result line also carries ``app``, a per-model ``models`` list (``id``,
``share_key``, ``state``, ``bytes``), and ``safety_gaps`` from
:func:`voxprint_common.mdl.readiness.shared_safety_gaps`; ``ready`` is false
when any safety gap exists and both commands exit 2. ``--dry-run`` builds the
plan and checks free disk space without downloading or enqueueing. For
``adopt``, ``--dry-run`` reports path and size matches and does not hash,
link, or copy. ``cleanup`` reports unless ``--apply`` is set, and ``--dry-run``
together with ``--apply`` is a usage error. Both global flags are accepted
before or after the subcommand. ``--gui`` belongs on ``download``, ``adopt``,
and ``verify``. ``verify --repair`` neutralises unsafe manifest paths and
downloads only the missing or corrupt models. ``verify --gui`` opens the
check-and-repair window. ``adopt --gui`` hashes in the progress window, one
file at a time.

Exit codes, shared with :mod:`voxprint_common.cli_protocol`:

* ``0``: success. The result ``ok`` field is true. ``ready`` on the result,
  and in the status file, tells an app it may open.
* ``1``: usage error.
* ``2``: the operation failed. Disk space, a digest mismatch, a network
  failure, and a non-empty ``safety_gaps`` list use this code. Nothing is
  downloaded when the disk check fails.
* ``3``: cancelled.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Mapping, Sequence
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
from voxprint_common.mdl.adopt import adopt
from voxprint_common.mdl.catalog import (
    SUITE_APPS,
    AppManifest,
    CatalogError,
    Model,
    PlannedFile,
    discover,
    listed_ids,
    load_manifest,
    model_share_key,
    models_for_download_all,
    plan_files,
    register_app,
    unregister_app,
)
from voxprint_common.mdl.cleanup import cleanup
from voxprint_common.mdl.events import TransferSession
from voxprint_common.mdl.gui_ipc import send as send_gui
from voxprint_common.mdl.hooks import hook_problems
from voxprint_common.mdl.index import (
    SuiteIndexError,
    load_index,
    verify_index_file,
    write_index_signature,
)
from voxprint_common.mdl.job import (
    Outcome,
    detect_tier,
    download,
    plan,
    verify,
    verify_repair,
)
from voxprint_common.mdl.names import CLI_NAME, DISPLAY_NAME
from voxprint_common.mdl.onnx_prebuilt import stale_rows
from voxprint_common.mdl.progress_ui import run_verify_repair, run_window
from voxprint_common.mdl.queue import (
    _already_installed,
    enqueue,
    enqueue_download_all,
    load_queue,
    run_or_attach,
)
from voxprint_common.mdl.readiness import publish_presence, shared_safety_gaps
from voxprint_common.mdl.repair_store import discover_parsed, planned, repair_store
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

    ``voxprint-mdl-gui`` opens Manage models (a default app if ``--app`` is
    omitted). ``--gui`` is implied for that entry point.
    ``voxprint-mdl --gui --app <id>`` does the same from the console script.

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
        ("ensure", "make one app's models ready through the shared store"),
        ("adopt", "link or copy local model files that match the descriptor"),
        (
            "repair-store",
            "adopt complete copies from app folders and rebuild stub views; never downloads",
        ),
        ("cleanup", "reclaim legacy copies that match a blob"),
        ("status", "print the status file"),
        ("verify", "re-hash the planned models"),
        ("worker", "keep the download window and the queue worker running"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--manifest",
            "--app-manifest",
            action="append",
            dest="manifest",
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
        if name in {"enqueue", "download", "ensure"}:
            command.add_argument(
                "--all",
                action="store_true",
                dest="all_models",
                help=(
                    "queue every model in the app's descriptor, including optional "
                    "ones; dropped ids such as MOSS are omitted"
                ),
            )
        if name in {"enqueue", "download", "plan"}:
            command.add_argument(
                "--all-tiers",
                action="store_true",
                dest="all_tiers",
                help=(
                    "include models above this card's VRAM tier; the plan warns "
                    "about VRAM instead of skipping them"
                ),
            )
        command.add_argument("--gui", action="store_true", help="show the progress window")
        if name == "status":
            command.add_argument(
                "--wait",
                action="store_true",
                help="run or attach until the queue is idle",
            )
        if name in {"adopt", "repair-store"}:
            command.add_argument(
                "--from",
                action="append",
                dest="sources",
                default=None,
                help="directory of local model files; repeat to search more than one",
            )
        if name == "cleanup":
            command.add_argument(
                "--legacy",
                action="append",
                default=None,
                help="legacy model directory; repeat to scan more than one",
            )
            command.add_argument(
                "--gc",
                action="store_true",
                help="report blobs that no view, app ref, or manifest names",
            )
            command.add_argument(
                "--apply",
                action="store_true",
                help="replace or remove the reported files; the default is a dry run",
            )
            command.add_argument(
                "--mode",
                choices=("link", "delete"),
                default="link",
                help="link replaces a match with a hardlink; delete removes a verified match",
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
    register = commands.add_parser(
        "register",
        help="record an installed app in state/installed-apps.json",
    )
    register.add_argument("--app", required=True, help="suite app id")
    register.add_argument("--path", required=True, help="install directory")
    register.add_argument("--display-name", default="", help="human-readable name")
    register.add_argument("--version", default="", help="installed app version")
    register.add_argument(
        "--descriptor",
        default="",
        help="mdl.json path if not at the install root",
    )
    unregister = commands.add_parser(
        "unregister",
        help="remove an app from state/installed-apps.json",
    )
    unregister.add_argument("--app", required=True, help="suite app id")
    clean_partials = commands.add_parser(
        "clean-partials",
        help="list orphan download partials; delete only with --apply",
    )
    clean_partials.add_argument(
        "--apply",
        action="store_true",
        help="delete listed orphans that no active job owns; default is a dry run",
    )
    open_tab = commands.add_parser(
        "open-tab",
        help="ask a running MDL window to show a tab, or open one",
    )
    open_tab.add_argument("--app", required=True, help="suite app id")
    open_tab.add_argument(
        "--tab",
        required=True,
        choices=("this_app", "shared"),
        help="manage-window tab to show",
    )
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
    if args.command == "register":
        return _register(args)
    if args.command == "unregister":
        return _unregister(args)
    if args.command == "clean-partials":
        return _clean_partials(args)
    if args.command == "open-tab":
        return _open_tab_command(args)
    if args.command == "worker":
        return _worker(args)
    if args.gui and args.command not in {"download", "verify", "adopt"}:
        return EXIT_USAGE, "--gui is only valid with download, adopt, or verify", {}
    if args.gui and args.command == "download":
        return _gui(args, session)
    if args.command == "status":
        if args.wait:
            return _wait(session)
        return _status(args)
    if args.command == "enqueue":
        return _enqueue(args)
    if args.command == "ensure":
        return _ensure(args, session)
    if args.command == "repair-store":
        return _repair_store_command(args)
    try:
        resolved_app, models, manifests = _request(args)
    except _Usage as exc:
        return EXIT_USAGE, exc.message, {}
    del resolved_app
    on_progress = _progress(session) if session is not None else None
    if args.command == "plan":
        outcome = plan(manifests, only=models, all_tiers=bool(args.all_tiers))
    elif args.command == "verify":
        if args.repair:
            notes = repair_manifest(dry_run=args.dry_run)
            if args.dry_run:
                text = "\n".join(notes) if notes else "nothing to neutralise"
                return EXIT_OK, text, {"ready": False}
            # Fold local copies into the store and relink views first, so
            # only what is truly absent reaches the network repair below.
            repair_store(manifests, only=models)
        if args.gui:
            return _verify_gui(args)
        if args.repair:
            outcome = verify_repair(models, repair=True, manifests=manifests)
        else:
            outcome = verify(manifests, only=models)
            return _verify_result(args, manifests, models, outcome)
    elif args.command == "adopt":
        return _adopt(args, on_progress)
    elif args.command == "cleanup":
        return _cleanup(args, on_progress)
    elif args.command == "download":
        if args.dry_run:
            outcome = download(
                manifests,
                dry_run=True,
                display=False,
                on_progress=on_progress,
                only=models,
                all_tiers=bool(args.all_tiers),
            )
        else:
            outcome = _queue_download(args, _watch(session))
    else:
        return EXIT_USAGE, f"unknown command {args.command}", {}
    return outcome.code, outcome.message, _payload(outcome.status)


def _adopt(
    args: argparse.Namespace,
    on_progress: object,
) -> tuple[int, str, dict[str, object]]:
    """Link or copy local files named by ``--from`` into the blob store."""
    sources = [Path(item) for item in (args.sources or [])]
    if not sources:
        return EXIT_USAGE, "adopt needs --from", {}
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    if manifests is None and args.app:
        manifests = [app.path for app in discover() if app.id == args.app]
        if not manifests:
            return EXIT_USAGE, f"no installed manifest for {args.app}", {}
    if manifests is None:
        return EXIT_USAGE, "adopt needs --app or --manifest", {}
    if args.app:
        for path in manifests:
            found = load_manifest(path)
            if found.app != args.app:
                return EXIT_USAGE, f"{path} is not the manifest for {args.app}", {}
    models = tuple(args.model) if args.model else None
    if args.gui:
        return _adopt_gui(manifests, sources, models, dry_run=bool(args.dry_run))
    outcome = adopt(
        manifests,
        sources,
        dry_run=bool(args.dry_run),
        on_progress=on_progress,  # type: ignore[arg-type]
        only=models,
    )
    return outcome.code, outcome.message, _adopt_payload(outcome.status)


def _adopt_gui(
    manifests: list[Path],
    sources: list[Path],
    models: tuple[str, ...] | None,
    *,
    dry_run: bool,
) -> tuple[int, str, dict[str, object]]:
    """Hash adopt candidates on the progress window's worker thread."""
    detach_console()

    def start(cancel: threading.Event, on_status: object) -> Outcome:
        return adopt(
            manifests,
            sources,
            dry_run=dry_run,
            on_status=on_status,  # type: ignore[arg-type]
            cancel=cancel,
            only=models,
        )

    try:
        code = run_window(start)
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}
    status = load_status()
    message = str(status.extra.get("summary") or status.error or status.state.value)
    return code, message, _adopt_payload(status)


def _adopt_payload(status: object) -> dict[str, object]:
    body = _payload(status)
    extra = getattr(status, "extra", {})
    for key in ("adopted", "reused", "skipped", "would_adopt", "duplicate"):
        rows = extra.get(key) if isinstance(extra, dict) else None
        body[key] = list(rows) if isinstance(rows, list) else []
    reclaimable = extra.get("reclaimable_bytes") if isinstance(extra, dict) else 0
    body["reclaimable_bytes"] = int(reclaimable) if isinstance(reclaimable, int) else 0
    return body


def _cleanup(
    args: argparse.Namespace,
    on_progress: object,
) -> tuple[int, str, dict[str, object]]:
    """Report or reclaim legacy copies. The default is a dry run."""
    if args.apply and args.dry_run:
        return EXIT_USAGE, "cleanup accepts either --apply or --dry-run", {}
    legacy = [Path(item) for item in (args.legacy or [])]
    if not legacy and not args.gc:
        return EXIT_USAGE, "cleanup needs --legacy or --gc", {}
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    if manifests is None and args.app:
        manifests = [app.path for app in discover() if app.id == args.app]
        if not manifests:
            return EXIT_USAGE, f"no installed manifest for {args.app}", {}
    if args.app and manifests is not None:
        for path in manifests:
            found = load_manifest(path)
            if found.app != args.app:
                return EXIT_USAGE, f"{path} is not the manifest for {args.app}", {}
    outcome = cleanup(
        legacy,
        manifests=manifests,
        app=args.app,
        mode=args.mode,
        apply=bool(args.apply),
        gc=bool(args.gc),
        on_progress=on_progress,  # type: ignore[arg-type]
    )
    return outcome.code, outcome.message, _cleanup_payload(outcome.status)


def _cleanup_payload(status: object) -> dict[str, object]:
    body = _payload(status)
    extra = getattr(status, "extra", {})
    keys = (
        "would_link",
        "linked",
        "would_delete",
        "deleted",
        "already",
        "unique",
        "skipped",
        "mismatch",
        "would_gc",
        "gced",
        "manifest",
        "removed_dirs",
    )
    for key in keys:
        rows = extra.get(key) if isinstance(extra, dict) else None
        body[key] = list(rows) if isinstance(rows, list) else []
    reclaimable = extra.get("reclaimable_bytes") if isinstance(extra, dict) else 0
    body["reclaimable_bytes"] = int(reclaimable) if isinstance(reclaimable, int) else 0
    body["cleanup_mode"] = extra.get("mode") if isinstance(extra, dict) else ""
    return body


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


def _worker(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Run the single worker; ``--gui`` also opens or shows the progress window."""
    code = serve(gui=bool(args.gui), app=args.app or "")
    return code, "", {}


def _enqueue(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    try:
        app, models, manifests = _request(args)
    except _Usage as exc:
        return EXIT_USAGE, exc.message, {}
    if app is None:
        return EXIT_USAGE, "enqueue needs --app or --manifest", {}
    if getattr(args, "all_models", False) and manifests:
        job_id = enqueue_download_all(app, manifests=manifests)
    else:
        job_id = enqueue(app, models, manifests=manifests, all_tiers=bool(args.all_tiers))
    # The job is already on the queue. Tell a running window to pick it
    # up; do not send ``enqueue`` or the GUI would queue the models twice.
    send_gui(app, {"cmd": "focus", "app": app}, timeout=0.2)
    send_gui(app, {"cmd": "wakeup"}, timeout=0.2)
    jobs = [job.to_dict() for job in load_queue()]
    return EXIT_OK, job_id, {"job": job_id, "ready": False, "state": "queued", "jobs": jobs}


_BLOCKING_STATES = frozenset({"missing", "corrupt", "stub"})
_STATE_RANK = {
    "ok": 0,
    "reused": 1,
    "downloaded": 2,
    "missing": 3,
    "corrupt": 4,
    "stub": 4,
    "unavailable": 5,
}
# Safety models gate the readiness check even when optional, so ensure must
# plan every safety-group row or a missing optional safety blob would fail
# the run closed without ever being queued.
_ENSURE_INCLUDE_GROUPS = ("safety",)


def _ensure(
    args: argparse.Namespace, session: Session | None
) -> tuple[int, str, dict[str, object]]:
    """Make one app's models ready without opening a window or prompting.

    The app id comes from the manifest; ``--app`` is optional and a mismatch
    is a usage error. Before enqueueing, complete copies that are already on
    disk (another app's folder such as ``model-forge``, a view, or a
    ``--from`` directory) are adopted into the shared store, so the job only
    fetches what is truly absent; adopted bytes count as ``reused``. After
    the job the views are relinked (blobs are trusted by their markers, no
    re-hash) and reported as ``views``. ``--dry-run`` plans and checks the
    disk without fetching, adopting, or relinking, and still reports the
    view states.
    """
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    if manifests is None and args.app:
        manifests = [app.path for app in discover() if app.id == args.app]
        if not manifests:
            return EXIT_USAGE, f"no installed manifest for {args.app}", {}
    if manifests is None:
        return EXIT_USAGE, "ensure needs --app-manifest, --manifest, or --app", {}
    app = load_manifest(manifests[0]).app
    if args.app and args.app != app:
        return EXIT_USAGE, f"{manifests[0]} is not the manifest for {args.app}", {}
    first = Path(manifests[0])
    install = first.parent if first.is_file() else first
    register_app(app, install, descriptor=first if first.is_file() else None)
    if getattr(args, "all_models", False) and args.model:
        return EXIT_USAGE, "--all cannot be combined with --model", {}
    models = (
        models_for_download_all(load_manifest(manifests[0]))
        if getattr(args, "all_models", False)
        else (tuple(args.model) if args.model else None)
    )
    parsed = [load_manifest(path) for path in manifests]
    if args.dry_run:
        outcome = plan(manifests, only=models, include_groups=_ENSURE_INCLUDE_GROUPS)
        ok = outcome.code == EXIT_OK
        files = planned(parsed, models, include_groups=_ENSURE_INCLUDE_GROUPS) if ok else []
        report = (
            repair_store(
                manifests,
                only=models,
                dry_run=True,
                include_groups=_ENSURE_INCLUDE_GROUPS,
            )
            if ok
            else None
        )
        rows = _model_rows(parsed, files, mode="dry-run", before=set(), only=models)
        code, payload = _readiness_payload(app, manifests, outcome.code, rows)
        body = _payload(outcome.status)
        body.update(payload)
        body["views"] = report.views if report is not None else []
        return code, outcome.message, body
    files = planned(parsed, models, include_groups=_ENSURE_INCLUDE_GROUPS)
    repair_store(manifests, only=models, include_groups=_ENSURE_INCLUDE_GROUPS)
    before = {item.sha256 for item in files if _already_installed(item)}
    job_id = enqueue(app, models, manifests=manifests, include_groups=_ENSURE_INCLUDE_GROUPS)
    outcome = run_or_attach(job_id, session=_watch(session))
    report = repair_store(manifests, only=models, include_groups=_ENSURE_INCLUDE_GROUPS)
    rows = _model_rows(parsed, files, mode="ensure", before=before, only=models)
    code, payload = _readiness_payload(app, manifests, outcome.code, rows)
    body = _payload(outcome.status)
    body.update(payload)
    body["views"] = report.views
    return code, outcome.message, body


def _verify_result(
    args: argparse.Namespace,
    manifests: Sequence[Path] | None,
    models: tuple[str, ...] | None,
    outcome: Outcome,
) -> tuple[int, str, dict[str, object]]:
    """Add the installer fields to the plain ``verify`` result.

    The models list re-plans what ``verify`` hashed, so the digest cache
    makes the extra pass cheap. A dry-run repair pass then inspects every
    app view: a model whose view is a stub (blob present, view invalid or
    absent) reports state ``stub``, and one whose view cannot be repaired
    because a blob is gone reports ``missing``; both block readiness.
    ``ready`` fails closed on a safety gap.
    """
    paths = list(manifests) if manifests is not None else None
    parsed = discover_parsed(paths)
    app = args.app or (parsed[0].app if len(parsed) == 1 else "")
    files = planned(parsed, models) if parsed else []
    rows = _model_rows(parsed, files, mode="verify", before=set(), only=models)
    report = (
        repair_store(paths, only=models, dry_run=True, include_groups=_ENSURE_INCLUDE_GROUPS)
        if parsed
        else None
    )
    if report is not None:
        _apply_view_states(rows, report.views, app)
    code, payload = _readiness_payload(app, paths, outcome.code, rows)
    body = _payload(outcome.status)
    body.update(payload)
    body["views"] = report.views if report is not None else []
    return code, outcome.message, body


def _apply_view_states(
    rows: list[dict[str, object]], view_rows: list[dict[str, object]], app: str
) -> None:
    """Downgrade model rows whose shared-store view is a stub or missing.

    A view row whose after-state is ``missing`` means a blob is gone, so
    the model row is ``missing``; ``would-repair`` means the view is a
    stub or absent while every blob is present or adoptable, so the model
    row is ``stub``. When ``app`` is non-empty only view rows for that
    app count, so another app's stub view cannot downgrade this app's
    model rows; an empty ``app`` (every discovered app) uses every view
    row. Rows without a view entry keep the hashed state.
    """
    for row in rows:
        entries = [
            view
            for view in view_rows
            if view["model"] == row["id"] and (not app or view["app"] == app)
        ]
        if any(view["after"] == "missing" for view in entries):
            row["state"] = "missing"
        elif any(view["after"] == "would-repair" for view in entries):
            row["state"] = "stub"


def _repair_store_command(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Adopt on-disk copies into the store and rebuild stub views.

    With no ``--manifest``/``--app-manifest``/``--app`` every installed
    app manifest is discovered and repaired — the one-time laptop
    migration. ``--app`` restricts discovery to that installed app, like
    ``ensure``. Nothing is downloaded; exit 2 when any file or view is
    still missing, so an installer can fall back to ``ensure``.
    """
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    if manifests is None and args.app:
        manifests = [app.path for app in discover() if app.id == args.app]
        if not manifests:
            return EXIT_USAGE, f"no installed manifest for {args.app}", {}
    if args.app:
        app = load_manifest(manifests[0]).app
        if args.app != app:
            return EXIT_USAGE, f"{manifests[0]} is not the manifest for {args.app}", {}
    models = tuple(args.model) if args.model else None
    extra_roots = [Path(item) for item in (args.sources or [])]
    report = repair_store(
        manifests,
        only=models,
        extra_roots=extra_roots,
        dry_run=bool(args.dry_run),
    )
    lines = [
        f"adopted {row['model']}: {row['path']}"
        for row in report.files
        if row["state"] == "adopted"
    ]
    lines += [
        f"view {row['after']}: {row['app']}/{row['model']}"
        for row in report.views
        if row["after"] in {"repaired", "missing"}
    ]
    lines.append(
        f"repair-store: {report.adopted} adopted, "
        f"{report.repaired_views} views repaired, {report.missing} missing"
    )
    code = EXIT_OK if report.ready else EXIT_FAILED
    return code, "\n".join(lines), report.to_json()


def _file_state(item: PlannedFile, mode: str, before: set[str]) -> str:
    """Return the installer state for one planned file.

    ``ensure`` distinguishes bytes that were already in the shared store
    (``reused``) from bytes fetched by this run (``downloaded``); ``verify``
    reports ``ok``; a dry run reports what is on disk without fetching. A
    file the plan needs but the store does not hold is ``missing`` (absent)
    or ``corrupt`` (present but wrong); the ``stub`` view state is applied
    to whole model rows by :func:`_apply_view_states`, not here.
    """
    if not _already_installed(item):
        return "corrupt" if item.destination.is_file() else "missing"
    if mode == "ensure":
        return "reused" if item.sha256 in before else "downloaded"
    if mode == "verify":
        return "ok"
    return "reused"


def _model_rows(
    parsed: Sequence[AppManifest],
    files: Sequence[PlannedFile],
    *,
    mode: str,
    before: set[str],
    only: tuple[str, ...] | None,
) -> list[dict[str, object]]:
    """Build one ``models`` entry per planned model row.

    Each entry carries the model ``id``, its ``share_key``, a ``state`` from
    ``ok`` / ``reused`` / ``downloaded`` / ``missing`` / ``corrupt`` /
    ``stub`` / ``unavailable``, and the model's total ``bytes``. Multi-file
    models merge into one row; the worst file state wins. Unavailable
    manifest rows are listed with state ``unavailable``.
    """
    owners: dict[tuple[str, str], Model] = {}
    for manifest in parsed:
        for model in manifest.models:
            for item in model.files:
                owners.setdefault((model.id, item.sha256), model)
    rows: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []
    for item in files:
        state = _file_state(item, mode, before)
        ids = item.model_ids or ([item.model_id] if item.model_id else [])
        for model_id in ids:
            model = owners.get((model_id, item.sha256))
            share_key = model_share_key(model) if model is not None else ""
            key = (model_id, share_key)
            row = rows.get(key)
            if row is None:
                row = {"id": model_id, "share_key": share_key, "state": state, "bytes": 0}
                rows[key] = row
                order.append(key)
            if _STATE_RANK[state] > _STATE_RANK[str(row["state"])]:
                row["state"] = state
            row["bytes"] = int(row["bytes"]) + item.size
    for manifest in parsed:
        for model in manifest.models:
            if not model.unavailable or (only is not None and model.id not in only):
                continue
            key = (model.id, model_share_key(model))
            if key not in rows:
                rows[key] = {
                    "id": model.id,
                    "share_key": key[1],
                    "state": "unavailable",
                    "bytes": 0,
                }
                order.append(key)
    return [rows[key] for key in order]


def _readiness_payload(
    app: str,
    manifest_paths: Sequence[Path] | None,
    outcome_code: int,
    rows: list[dict[str, object]],
) -> tuple[int, dict[str, object]]:
    """Combine the model rows with the fail-closed safety gate.

    ``ready`` is false when the outcome failed, when any model row is
    missing or corrupt, or when :func:`shared_safety_gaps` reports a gap. A
    ready-false result uses exit code 2 so an installer never opens the app.
    """
    gaps = shared_safety_gaps(app, manifests=list(manifest_paths) if manifest_paths else None)
    ready = (
        outcome_code == EXIT_OK
        and all(str(row["state"]) not in _BLOCKING_STATES for row in rows)
        and not gaps
    )
    code = outcome_code if outcome_code != EXIT_OK else (EXIT_OK if ready else EXIT_FAILED)
    payload: dict[str, object] = {
        "app": app,
        "ready": ready,
        "models": rows,
        "safety_gaps": [
            {"share_key": gap.share_key, "model_id": gap.model_id, "state": gap.state}
            for gap in gaps
        ],
    }
    return code, payload


def _queue_download(args: argparse.Namespace, session: TransferSession | None) -> Outcome:
    try:
        app, models, manifests = _request(args)
    except _Usage as exc:
        return Outcome(EXIT_USAGE, exc.message, load_status())
    all_tiers = bool(getattr(args, "all_tiers", False))
    if app is not None:
        job_id = enqueue(app, models, manifests=manifests, all_tiers=all_tiers)
        return run_or_attach(job_id, session=session, display=args.gui)
    if not _busy():
        found = discover()
        if not found and not load_queue():
            return download(
                None, display=args.gui, session=session, only=models, all_tiers=all_tiers
            )
        known = {item.id: listed_ids(load_manifest(item.path)) for item in found}
        if models is not None and set(models).difference(*known.values()):
            return download(None, display=False, dry_run=True, only=models)
        for installed in found:
            listed = None
            if models is not None:
                listed = tuple(model for model in models if model in known[installed.id])
                if not listed:
                    continue
            enqueue(installed.id, listed, all_tiers=all_tiers)
    return run_or_attach(None, session=session, display=args.gui)


class _Usage(Exception):
    """A command-line usage error that `_run` maps to exit code 1."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _request(
    args: argparse.Namespace,
) -> tuple[str | None, tuple[str, ...] | None, list[Path] | None]:
    manifests = [Path(item) for item in args.manifest] if args.manifest else None
    app = args.app or None
    if app is None and manifests:
        app = load_manifest(manifests[0]).app
    if getattr(args, "all_models", False):
        if args.model:
            raise _Usage("--all cannot be combined with --model")
        if manifests is None and app:
            found = next((item for item in discover() if item.id == app), None)
            if found is None:
                raise _Usage(f"no installed manifest for {app}")
            manifests = [found.path]
        if not manifests:
            raise _Usage("--all needs --app or --manifest")
        app = app or load_manifest(manifests[0]).app
        models = models_for_download_all(load_manifest(manifests[0]))
        return app, models, manifests
    models = tuple(args.model) if args.model else None
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
    commands = {
        "plan",
        "enqueue",
        "download",
        "ensure",
        "adopt",
        "repair-store",
        "cleanup",
        "status",
        "verify",
        "worker",
    }
    return "--gui" in argv and not any(token in commands for token in argv)


def _open_tab_command(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Ask a running GUI to show a tab, or open the manage window on it."""
    app = args.app
    message = {"cmd": "open-tab", "app": app, "tab": args.tab}
    reply = send_gui(app, message, timeout=0.5)
    if reply and reply.get("ok"):
        return EXIT_OK, "ok", {"ok": True, "app": app, "tab": args.tab}
    try:
        outcome = run_manage_window(app, tab=args.tab)
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra ({exc})", {}
    except TypeError:
        outcome = run_manage_window(app)
    return _window_code(outcome), "ok" if outcome is WindowResult.READY else "", {"tab": args.tab}


def _default_manage_app() -> str:
    """Return the first installed app id, or the first suite app if none."""
    try:
        found = discover()
    except CatalogError:
        found = []
    if found:
        return found[0].id
    return SUITE_APPS[0].id


def _open_named_window(argv: Sequence[str]) -> int:
    detach_console()
    tokens = list(argv)
    app = _flag_value(tokens, "--app") or _default_manage_app()
    manifest = _flag_value(tokens, "--manifest")
    tab = _flag_value(tokens, "--tab")
    message = {
        "cmd": "open-tab" if tab else "manage",
        "app": app,
        "manifest": manifest,
        "tab": tab or "this_app",
    }
    reply = send_gui(app, message, timeout=0.5)
    if reply and reply.get("ok"):
        return EXIT_OK
    try:
        outcome = run_manage_window(app, manifest=manifest or None, tab=tab or None)
    except ImportError as exc:
        print(f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", file=sys.stderr)
        return EXIT_FAILED
    except TypeError:
        outcome = run_manage_window(app, manifest=manifest or None)
    return _window_code(outcome)


def _flag_value(tokens: Sequence[str], flag: str) -> str:
    if flag in tokens:
        index = tokens.index(flag)
        if index + 1 < len(tokens):
            return tokens[index + 1]
    prefix = f"{flag}="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix) :]
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
    try:
        app_id, models, manifest_paths = _request(args)
    except _Usage as exc:
        return EXIT_USAGE, exc.message, {}
    app = app_id or args.app or "suite"
    manifests = list(manifest_paths or [])
    prior = {job.id: job.state for job in load_queue()}
    started_after = _invocation_mark()
    try:
        outcome = run_download_window(
            app,
            manifest=manifests[0] if manifests else None,
            manifests=manifests or None,
            models=models,
        )
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}
    status = load_status()
    scoped = _scoped_error(status, prior=prior, started_after=started_after)
    if outcome is WindowResult.FAILED:
        # A scoped job failure clears status.error so `status` stays ready.
        # Prefer that job error; only blame the window when nothing failed.
        message = scoped or status.error or "the download window could not be shown"
        return EXIT_FAILED, message, _payload(status)
    if scoped:
        return EXIT_FAILED, scoped, _payload(status)
    message = status.error or ("ready" if status.ready else status.state.value)
    return _window_code(outcome), message, _payload(status)


def _invocation_mark() -> str:
    """Return a local ISO timestamp used to ignore jobs queued before this run."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _scoped_error(
    status: object,
    *,
    prior: Mapping[str, str] | None = None,
    started_after: str = "",
) -> str:
    """Return this invocation's queue-job error, if any.

    A failed plan or download for one model leaves overall ``state`` ready
    so ``status`` does not reprint the error. The message still lives on
    the job, and ``download --gui`` must surface it with exit code 2.
    Older failed jobs already in the queue or ``status.jobs`` are ignored
    unless they belong to this run (a new id, or a known job that was not
    already failed when this command started).
    """
    known = dict(prior or {})

    def current_failure(job_id: str, state: str, error: str, enqueued_at: str = "") -> str:
        text = error.strip()
        if state != "failed" or not text:
            return ""
        previous = known.get(job_id)
        if previous == "failed":
            return ""
        if previous is not None:
            return text
        if started_after and enqueued_at and enqueued_at < started_after:
            return ""
        return text

    for row in getattr(status, "jobs", None) or []:
        if not isinstance(row, dict):
            continue
        found = current_failure(
            str(row.get("id") or ""),
            str(row.get("state") or ""),
            str(row.get("error") or ""),
            str(row.get("enqueued_at") or ""),
        )
        if found:
            return found
    for job in load_queue():
        found = current_failure(job.id, job.state, job.error, job.enqueued_at)
        if found:
            return found
    return ""


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


def _register(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Write one row to installed-apps.json."""
    descriptor = Path(args.descriptor) if str(args.descriptor).strip() else None
    found = register_app(
        args.app,
        Path(args.path),
        display_name=str(args.display_name or ""),
        version=str(args.version or ""),
        descriptor=descriptor,
    )
    return EXIT_OK, f"registered {found.id}", {"id": found.id, "path": str(found.path)}


def _unregister(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """Remove one row from installed-apps.json."""
    removed = unregister_app(args.app)
    if not removed:
        return EXIT_OK, f"{args.app} was not registered", {"removed": False}
    return EXIT_OK, f"unregistered {args.app}", {"removed": True}


def _clean_partials(args: argparse.Namespace) -> tuple[int, str, dict[str, object]]:
    """List orphan partials, or delete them when ``--apply`` is set."""
    from voxprint_common.mdl.partials import clean_partials

    report = clean_partials(apply=bool(args.apply))
    message = (
        "nothing to clean"
        if not report.rows
        else (f"{'removed' if args.apply else 'would remove'} {len(report.rows)} orphan partial(s)")
    )
    return EXIT_OK, message, report.to_dict()
