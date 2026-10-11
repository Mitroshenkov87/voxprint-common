"""``voxprint-mdl`` command line.

Subcommands: ``plan``, ``download``, ``status``, ``verify``.

``--json`` writes UTF-8 progress lines and one result line on stdout. Human
text goes to stderr. ``--dry-run`` builds the plan and checks free disk space
without downloading. Both flags are accepted before or after the subcommand.
``--gui`` belongs on ``download``.

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

from voxprint_common import __version__
from voxprint_common.cli_protocol import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    Session,
    emit,
    result,
)
from voxprint_common.mdl.job import download, plan, verify
from voxprint_common.mdl.names import CLI_NAME, DISPLAY_NAME
from voxprint_common.mdl.status import JobState, StatusError, load_status

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

    Args:
        argv: Arguments without the program name. Defaults to ``sys.argv[1:]``.
    """
    args_list = list(sys.argv[1:] if argv is None else argv)
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
    except StatusError as exc:
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
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "show the models this machine needs and check free disk space"),
        ("download", "download the planned models"),
        ("status", "print the status file"),
        ("verify", "re-hash the planned models"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--manifest",
            action="append",
            default=[],
            help="path to an app mdl.json; default is to discover installed apps",
        )
        command.add_argument("--gui", action="store_true", help="show the progress window")
    return parser


def _run(args: argparse.Namespace, session: Session | None) -> tuple[int, str, dict[str, object]]:
    manifests = args.manifest or None
    if args.gui and args.command != "download":
        return EXIT_USAGE, "--gui is only valid with download", {}
    if args.gui:
        return _gui(args, session)
    if args.command == "status":
        return _status()
    on_progress = _progress(session) if session is not None else None
    if args.command == "plan":
        outcome = plan(manifests)
    elif args.command == "verify":
        outcome = verify(manifests)
    elif args.command == "download":
        outcome = download(
            manifests,
            dry_run=args.dry_run,
            display=False,
            on_progress=on_progress,
        )
    else:
        return EXIT_USAGE, f"unknown command {args.command}", {}
    return outcome.code, outcome.message, _payload(outcome.status)


def _gui(args: argparse.Namespace, session: Session | None) -> tuple[int, str, dict[str, object]]:
    del session
    try:
        from voxprint_common.mdl.progress_ui import run_window
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}

    def start(cancel: object, on_status: object) -> object:
        return download(
            args.manifest or None,
            dry_run=args.dry_run,
            cancel=cancel,  # type: ignore[arg-type]
            display=True,
            on_status=on_status,  # type: ignore[arg-type]
        )

    try:
        code = run_window(start)  # type: ignore[arg-type]
    except ImportError as exc:
        return EXIT_FAILED, f"{DISPLAY_NAME} needs the ui extra for --gui ({exc})", {}
    status = load_status()
    message = status.error or ("ready" if status.ready else status.state.value)
    return code, message, _payload(status)


def _status() -> tuple[int, str, dict[str, object]]:
    status = load_status()
    message = status.error or ("ready" if status.ready else status.state.value)
    return EXIT_OK, message, _payload(status)


def _payload(status: object) -> dict[str, object]:
    ready = bool(getattr(status, "ready", False))
    state = getattr(status, "state", JobState.IDLE)
    value = state.value if isinstance(state, JobState) else str(state)
    return {"ready": ready, "state": value}


def _progress(session: Session):
    def on_progress(fraction: float | None, message: str) -> None:
        session.progress("download", fraction, message)

    return on_progress
