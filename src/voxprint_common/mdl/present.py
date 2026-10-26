"""Text, errors, and progress for the model-downloader window.

Nothing in this module imports Qt. The window paints :class:`WindowView`.
Technical exception text stays on :class:`Explanation.technical` and in the
log. The sentences a person reads come from :mod:`voxprint_common.i18n`.
"""

from __future__ import annotations

import math
import os
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from voxprint_common.branding import identity_colour
from voxprint_common.i18n import get_language, tr, tr_lang
from voxprint_common.mdl.catalog import DiskShort
from voxprint_common.mdl.fetch import Cancelled, FetchError
from voxprint_common.ui.splash import identity

__all__ = [
    "CONCURRENCY",
    "ERROR_KEYS",
    "SOURCE_IDS",
    "STATE_KEYS",
    "AppSlot",
    "ErrorCode",
    "Explanation",
    "GroupView",
    "ItemState",
    "ManageRow",
    "ManageView",
    "QueueItem",
    "RowView",
    "SmoothedProgress",
    "Snapshot",
    "WindowView",
    "display_path",
    "explain",
    "format_eta",
    "format_size",
    "format_speed",
    "localized_name",
    "source_id",
    "source_label",
    "view_for",
]

CONCURRENCY = 2
SOURCE_IDS = ("huggingface", "kaggle", "modelscope", "gdrive", "url", "local", "unknown")
STATE_KEYS = {
    "pending": "mdl.state.pending",
    "downloading": "mdl.state.downloading",
    "verifying": "mdl.state.verifying",
    "ready": "mdl.state.ready",
    "failed": "mdl.state.failed",
    "paused": "mdl.state.paused",
}


class ItemState(StrEnum):
    """One queue row."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"
    PAUSED = "paused"


class ErrorCode(StrEnum):
    """A calm explanation, never a traceback."""

    NETWORK = "network"
    MIRROR = "mirror"
    DISK = "disk"
    CHECKSUM = "checksum"
    CANCELLED = "cancelled"
    GENERIC = "generic"


ERROR_KEYS = {
    ErrorCode.NETWORK: ("mdl.error.network.what", "mdl.error.network.do"),
    ErrorCode.MIRROR: ("mdl.error.mirror.what", "mdl.error.mirror.do"),
    ErrorCode.DISK: ("mdl.error.disk.what", "mdl.error.disk.do"),
    ErrorCode.CHECKSUM: ("mdl.error.checksum.what", "mdl.error.checksum.do"),
    ErrorCode.CANCELLED: ("mdl.error.cancelled.what", "mdl.error.cancelled.do"),
    ErrorCode.GENERIC: ("mdl.error.generic.what", "mdl.error.generic.do"),
}


@dataclass
class Explanation:
    """Localized keys plus the technical text kept behind Details."""

    code: ErrorCode
    what_key: str
    do_key: str
    params: dict[str, str]
    technical: str
    severity: str = "error"


@dataclass
class QueueItem:
    """One deduplicated file in the persistent queue."""

    key: str
    names: dict[str, str]
    fallback: str
    size: int
    received: int
    state: ItemState
    source: str
    speed_bps: float
    eta_s: float | None
    apps: list[str]
    error_code: str = ""
    technical: str = ""
    destination: str = ""
    urls: list[str] = field(default_factory=list)
    version: str = ""
    component_id: str = ""
    optional: bool = False
    sha256: str = ""
    attempts: int = 0
    error_params: dict[str, str] = field(default_factory=dict)

    def copy(self) -> QueueItem:
        """Return a detached copy safe to hand to another thread."""
        return QueueItem(
            key=self.key,
            names=dict(self.names),
            fallback=self.fallback,
            size=self.size,
            received=self.received,
            state=self.state,
            source=self.source,
            speed_bps=self.speed_bps,
            eta_s=self.eta_s,
            apps=list(self.apps),
            error_code=self.error_code,
            technical=self.technical,
            destination=self.destination,
            urls=list(self.urls),
            version=self.version,
            component_id=self.component_id,
            optional=self.optional,
            sha256=self.sha256,
            attempts=self.attempts,
            error_params=dict(self.error_params),
        )


@dataclass
class AppSlot:
    """One app whose files are in the queue."""

    app_id: str
    open_cmd: str = ""


@dataclass
class Snapshot:
    """The queue as the window and the status file should show it."""

    items: list[QueueItem] = field(default_factory=list)
    apps: list[AppSlot] = field(default_factory=list)
    paused: bool = False
    held: bool = False
    note_code: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    boot_code: str = ""
    boot_technical: str = ""
    boot_params: dict[str, str] = field(default_factory=dict)

    def recompute(self) -> None:
        """Refresh byte totals from the unique rows."""
        self.bytes_total = sum(item.size for item in self.items)
        self.bytes_done = sum(min(item.received, item.size) for item in self.items)

    def fraction(self) -> float:
        """Return bytes done over bytes total, from 0 to 1."""
        if self.bytes_total <= 0:
            if self.items and all(item.state is ItemState.READY for item in self.items):
                return 1.0
            return 0.0
        return min(1.0, self.bytes_done / self.bytes_total)

    def for_app(self, app_id: str) -> list[QueueItem]:
        """Return the rows that belong to ``app_id``."""
        return [item for item in self.items if app_id in item.apps]

    def app_ready(self, app_id: str) -> bool:
        """Return whether every file for ``app_id`` has been checked.

        An app with no rows is not ready: nothing has been planned for it yet.
        """
        rows = self.for_app(app_id)
        return bool(rows) and all(item.state is ItemState.READY for item in rows)

    def app_failed(self, app_id: str) -> bool:
        """Return whether any file for ``app_id`` failed."""
        return any(item.state is ItemState.FAILED for item in self.for_app(app_id))

    def copy(self) -> Snapshot:
        """Return a detached copy."""
        snap = Snapshot(
            items=[item.copy() for item in self.items],
            apps=[AppSlot(slot.app_id, slot.open_cmd) for slot in self.apps],
            paused=self.paused,
            held=self.held,
            note_code=self.note_code,
            bytes_done=self.bytes_done,
            bytes_total=self.bytes_total,
            boot_code=self.boot_code,
            boot_technical=self.boot_technical,
            boot_params=dict(self.boot_params),
        )
        return snap


@dataclass(frozen=True)
class RowView:
    """One painted line."""

    key: str
    title: str
    meta: str
    state: str
    state_label: str
    action: str = ""
    action_label: str = ""
    action_arg: str = ""
    note: str = ""


@dataclass(frozen=True)
class GroupView:
    """Rows that belong to one app, under that app's motif."""

    app_id: str
    title: str
    colour: str
    motif: str
    headline: str
    ready: bool
    rows: tuple[RowView, ...]
    open_label: str = ""


@dataclass(frozen=True)
class ManageRow:
    """One model on the manage-models page."""

    key: str
    title: str
    meta: str
    state: str
    state_label: str
    action: str
    action_label: str
    note: str = ""


@dataclass(frozen=True)
class ManageView:
    """Installed models and the extras this graphics card can run."""

    installed: tuple[ManageRow, ...]
    available: tuple[ManageRow, ...]
    disk: str
    empty_installed: str
    empty_available: str


@dataclass(frozen=True)
class WindowView:
    """Everything the window paints for one language."""

    mode: str
    focus_app: str
    colour: str
    motif: str
    heading: str
    subtitle: str
    groups: tuple[GroupView, ...]
    overall: float
    overall_caption: str
    overall_detail: str
    status: str
    error_what_title: str
    error_what: str
    error_do_title: str
    error_do: str
    technical: str
    show_error: bool
    show_retry: bool
    paused: bool
    show_pause: bool
    show_cancel: bool
    show_open: bool
    open_label: str
    open_app: str
    all_set: bool
    manage_label: str
    back_label: str
    pause_label: str
    cancel_label: str
    retry_label: str
    close_label: str
    details_label: str
    details_hide_label: str
    manage: ManageView | None = None
    background: str = ""
    error_path: str = ""
    open_folder_label: str = ""
    footer: str = ""


class SmoothedProgress:
    """Ease a byte fraction so the bar never jumps.

    A tick moves only part of the way to the target, and never more than
    eight percent of the bar. A backwards correction eases down the same way.
    """

    def __init__(self, tau: float = 0.45, cap: float = 0.08) -> None:
        """Start the bar at zero.

        Args:
            tau: Seconds for an exponential ease. Larger is slower.
            cap: Maximum change in one tick, as a fraction of the bar.
        """
        self.tau = tau
        self.cap = cap
        self.shown = 0.0
        self.target = 0.0

    def set_target(self, value: float) -> None:
        """Set the byte fraction the bar is easing toward.

        Args:
            value: From 0 to 1. Values outside that range are clamped.
        """
        self.target = min(1.0, max(0.0, value))

    def tick(self, dt: float) -> float:
        """Move the shown fraction and return it.

        Args:
            dt: Seconds since the previous tick. Zero leaves the bar where it is.
        """
        if dt <= 0:
            return self.shown
        delta = self.target - self.shown
        if abs(delta) < 1e-4:
            self.shown = self.target
            return self.shown
        step = delta * (1.0 - math.exp(-dt / self.tau))
        if step > self.cap:
            step = self.cap
        elif step < -self.cap:
            step = -self.cap
        self.shown += step
        if abs(self.target - self.shown) < 1e-4:
            self.shown = self.target
        return self.shown


def localized_name(names: dict[str, str], fallback: str, lang: str | None = None) -> str:
    """Return a manifest name for ``lang``, then English, then ``fallback``.

    Args:
        names: Language code to display name.
        fallback: Text used when the manifest has no name.
        lang: Language code. Defaults to the active UI language.
    """
    code = (lang or _active())[:2]
    if names.get(code):
        return names[code]
    if names.get("en"):
        return names["en"]
    return fallback or code


_COMMA_LANGS = frozenset({"de", "lv", "ru", "uk"})


def _format_number(value: float, *, decimals: int, lang: str | None) -> str:
    code = (lang or _active())[:2]
    comma = code in _COMMA_LANGS
    if decimals <= 0:
        whole = str(round(value))
        fraction = ""
    else:
        whole, fraction = f"{value:.{decimals}f}".split(".", 1)
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    if len(whole) > 3:
        chunks: list[str] = []
        while whole:
            chunks.append(whole[-3:])
            whole = whole[:-3]
        whole = ("\u202f" if comma else ",").join(reversed(chunks))
    if not fraction:
        return sign + whole
    return f"{sign}{whole}{',' if comma else '.'}{fraction}"


def display_path(path: Path | str) -> str:
    """Return ``path`` with the separators of the system it names.

    A Windows drive path such as ``C:/Program Files/...`` is shown with
    backslashes, including when the manage window is painted off Windows.
    A POSIX path keeps its forward slashes.

    Args:
        path: Directory the disk line names.
    """
    text = str(path)
    if os.name == "nt" or (len(text) >= 2 and text[0].isalpha() and text[1] == ":"):
        return str(PureWindowsPath(text))
    return text


def format_size(amount: int, *, lang: str | None = None) -> str:
    """Return a short size such as ``1.5 MB`` or ``457,8 МБ``.

    German, Russian, Ukrainian, and Latvian use a decimal comma and a narrow
    no-break space before the unit. English uses a dot and a normal space.

    Args:
        amount: Size in bytes.
        lang: Language code. Defaults to the active UI language.
    """
    value = float(max(0, amount))
    units = ("mdl.unit.b", "mdl.unit.kb", "mdl.unit.mb", "mdl.unit.gb")
    code = (lang or _active())[:2]
    gap = "\u202f" if code in _COMMA_LANGS else " "
    for index, key in enumerate(units):
        if value < 1024 or key == "mdl.unit.gb":
            number = _format_number(value, decimals=0 if index == 0 else 1, lang=lang)
            return f"{number}{gap}{_t(key, lang)}"
        value /= 1024
    number = _format_number(value, decimals=1, lang=lang)
    return f"{number}{gap}{_t('mdl.unit.gb', lang)}"


def format_speed(bps: float, *, lang: str | None = None) -> str:
    """Return a throughput label, or a dash when nothing has been measured.

    Args:
        bps: Bytes per second.
        lang: Language code. Defaults to the active UI language.
    """
    if bps <= 0 or math.isnan(bps):
        return _t("mdl.eta.unknown", lang)
    size = format_size(int(bps), lang=lang)
    return _t("mdl.unit.per_second", lang, size=size)


def format_eta(seconds: float | None, *, lang: str | None = None) -> str:
    """Return a remaining-time label, or a dash when it is unknown.

    Args:
        seconds: Remaining seconds.
        lang: Language code. Defaults to the active UI language.
    """
    if seconds is None or seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
        return _t("mdl.eta.unknown", lang)
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return _t("mdl.eta.hours", lang, hours=hours, minutes=minutes)
    if minutes:
        return _t("mdl.eta.minutes", lang, minutes=minutes, seconds=secs)
    return _t("mdl.eta.seconds", lang, seconds=secs)


def source_id(url_or_host: str) -> str:
    """Return a source id for a URL or host name.

    Args:
        url_or_host: A mirror URL or the host already extracted from one.
    """
    text = url_or_host.strip()
    if not text:
        return "unknown"
    if text in SOURCE_IDS:
        return text
    host = urllib.parse.urlparse(text).netloc or text
    host = host.lower().split(":", 1)[0]
    if host in {"huggingface.co", "hf.co", "cdn-lfs.huggingface.co"} or host.endswith(
        ".huggingface.co"
    ):
        return "huggingface"
    if "kaggle" in host:
        return "kaggle"
    if "modelscope" in host:
        return "modelscope"
    if "drive.google." in host or host.endswith("googleusercontent.com") or "docs.google." in host:
        return "gdrive"
    if text == "local" or host == "local":
        return "local"
    return "url"


def source_label(source: str, *, lang: str | None = None) -> str:
    """Return the friendly source name.

    Args:
        source: A source id or a URL.
        lang: Language code. Defaults to the active UI language.
    """
    code = source if source in SOURCE_IDS else source_id(source)
    return _t(f"mdl.source.{code}", lang)


def explain(
    error: BaseException | str,
    *,
    path: str = "",
    need: int = 0,
    free: int = 0,
    lang: str | None = None,
) -> Explanation:
    """Map a failure to a calm explanation.

    The returned sentences do not contain a traceback. ``technical`` keeps the
    exception text for the details expander and the log file.

    Args:
        error: An exception or a message stored in the status file.
        path: Disk folder, used for a short-disk explanation.
        need: Bytes required, including headroom.
        free: Bytes free.
        lang: Unused. Sentences are translated when the view is built.
    """
    del lang
    technical = _technical(error)
    if isinstance(error, DiskShort):
        path = str(error.path)
        need = error.need
        free = error.free
    code = _code_for(error, technical)
    what_key, do_key = ERROR_KEYS[code]
    params: dict[str, str] = {}
    if code is ErrorCode.DISK:
        params = {
            "path": path or "—",
            "need_bytes": str(max(0, need)),
            "free_bytes": str(max(0, free)),
        }
    severity = "note" if code is ErrorCode.MIRROR else "error"
    return Explanation(code, what_key, do_key, params, technical, severity)


def view_for(
    snapshot: Snapshot,
    *,
    mode: str = "queue",
    focus_app: str = "",
    manage: ManageView | None = None,
    lang: str | None = None,
    background: str = "",
) -> WindowView:
    """Build the painted view for ``snapshot``.

    Args:
        snapshot: Current queue.
        mode: ``queue`` or ``manage``.
        focus_app: The app this window was opened for.
        manage: Manage-models page. Shown when ``mode`` is ``manage``.
        lang: Language code. Defaults to the active UI language.
        background: Note shown when downloads continue without the window.
    """
    code = lang or _active()
    focus = focus_app or (snapshot.apps[0].app_id if snapshot.apps else "")
    mark = identity(focus) if focus else identity("voxprint")
    groups = tuple(_group(slot, snapshot, code) for slot in snapshot.apps)
    overall = snapshot.fraction()
    done = format_size(snapshot.bytes_done, lang=code)
    total = format_size(snapshot.bytes_total, lang=code)
    remaining = _overall_eta(snapshot)
    if snapshot.paused:
        detail = _t("mdl.overall.paused", code, done=done, total=total)
    elif remaining is None:
        detail = _t("mdl.overall.amounts", code, done=done, total=total)
    else:
        detail = _t(
            "mdl.overall.detail",
            code,
            done=done,
            total=total,
            eta=format_eta(remaining, lang=code),
        )
    if snapshot.items:
        detail = f"{detail} · {_t('mdl.files', code, count=len(snapshot.items))}"
    explanation = _active_explanation(snapshot, code)
    show_error = explanation is not None and explanation.severity == "error"
    all_ready = bool(snapshot.apps) and all(
        snapshot.app_ready(slot.app_id) for slot in snapshot.apps
    )
    focus_ready = bool(focus) and snapshot.app_ready(focus)
    busy = any(
        item.state
        in {ItemState.PENDING, ItemState.DOWNLOADING, ItemState.VERIFYING, ItemState.PAUSED}
        for item in snapshot.items
    )
    failed = any(item.state is ItemState.FAILED for item in snapshot.items)
    all_set = focus_ready and not busy and not failed and mode != "manage"
    if not focus and all_ready and not busy:
        all_set = True
    heading, subtitle, status = _headings(
        snapshot, code, focus, mark.display_name, all_set, mode, explanation
    )
    open_app = focus if focus_ready else ""
    if all_set and not open_app and snapshot.apps:
        open_app = snapshot.apps[0].app_id
    one_app = len(snapshot.apps) == 1
    error_path, technical = _error_path(explanation)
    folder_label = (
        _t("mdl.open_folder", code)
        if error_path and explanation is not None and explanation.code is ErrorCode.DISK
        else ""
    )
    return WindowView(
        mode=mode,
        focus_app=focus,
        colour=identity_colour(focus) if focus else mark.colour,
        motif=mark.motif.value,
        heading=heading,
        subtitle=subtitle,
        groups=groups,
        overall=overall,
        overall_caption=_t("mdl.overall", code),
        overall_detail=detail,
        status=status,
        error_what_title=_t("mdl.error.what", code),
        error_what=_sentence(explanation, "what", code) if explanation else "",
        error_do_title=_t("mdl.error.do", code),
        error_do=_sentence(explanation, "do", code) if explanation else "",
        technical=technical,
        show_error=show_error,
        show_retry=show_error or snapshot.held,
        paused=snapshot.paused,
        show_pause=busy and not all_set,
        show_cancel=busy and not snapshot.held,
        show_open=bool(open_app) and mode != "manage" and (all_set or one_app),
        open_label=_t("mdl.open", code, app=identity(open_app).display_name) if open_app else "",
        open_app=open_app,
        all_set=all_set,
        manage_label=_t("mdl.manage.open", code),
        back_label=_t("mdl.manage.back", code),
        pause_label=_t("mdl.resume", code)
        if snapshot.paused or snapshot.held
        else _t("mdl.pause", code),
        cancel_label=_t("mdl.cancel", code),
        retry_label=_t("mdl.retry", code) if show_error else _t("mdl.resume", code),
        close_label=_t("mdl.close", code),
        details_label=_t("mdl.details", code),
        details_hide_label=_t("mdl.details.hide", code),
        manage=manage,
        background=background,
        error_path=error_path,
        open_folder_label=folder_label,
        footer=_t("mdl.footer", code),
    )


def _group(slot: AppSlot, snapshot: Snapshot, lang: str) -> GroupView:
    mark = identity(slot.app_id)
    rows = snapshot.for_app(slot.app_id)
    ready = snapshot.app_ready(slot.app_id)
    failed = snapshot.app_failed(slot.app_id)
    paused = snapshot.paused or any(item.state is ItemState.PAUSED for item in rows)
    downloading = any(item.state in {ItemState.DOWNLOADING, ItemState.VERIFYING} for item in rows)
    if failed:
        headline = _t("mdl.group.failed", lang, app=mark.display_name)
    elif ready:
        headline = _t("mdl.group.ready", lang, app=mark.display_name)
    elif paused:
        headline = _t("mdl.group.paused", lang, app=mark.display_name)
    elif downloading:
        headline = _t("mdl.group.downloading", lang, app=mark.display_name)
    else:
        headline = _t("mdl.group.waiting", lang, app=mark.display_name)
    painted = tuple(_row(item, lang) for item in rows)
    return GroupView(
        app_id=slot.app_id,
        title=mark.display_name,
        colour=identity_colour(slot.app_id),
        motif=mark.motif.value,
        headline=headline,
        ready=ready,
        rows=painted,
        open_label=_t("mdl.open_short", lang) if ready else "",
    )


def _row(item: QueueItem, lang: str) -> RowView:
    title = localized_name(item.names, item.fallback, lang)
    parts = [format_size(item.size, lang=lang)]
    if item.state in {
        ItemState.DOWNLOADING,
        ItemState.VERIFYING,
        ItemState.READY,
        ItemState.PAUSED,
    }:
        label = source_label(item.source, lang=lang)
        if source_id(item.source) == "local":
            parts.append(label)
        else:
            parts.append(_t("mdl.row.from", lang, source=label))
    if item.state is ItemState.DOWNLOADING and item.speed_bps > 0:
        parts.append(format_speed(item.speed_bps, lang=lang))
        eta = format_eta(item.eta_s, lang=lang)
        if item.eta_s is not None and eta != _t("mdl.eta.unknown", lang):
            parts.append(eta)
    meta = " · ".join(parts)
    note = ""
    if len(item.apps) > 1:
        others = ", ".join(identity(app_id).display_name for app_id in item.apps)
        note = _t("mdl.manage.shared", lang, apps=others)
    return RowView(
        key=item.key,
        title=title,
        meta=meta,
        state=item.state.value,
        state_label=_t(STATE_KEYS[item.state.value], lang),
        note=note,
    )


def _headings(
    snapshot: Snapshot,
    lang: str,
    focus: str,
    focus_name: str,
    all_set: bool,
    mode: str,
    explanation: Explanation | None,
) -> tuple[str, str, str]:
    if mode == "manage":
        name = identity(focus).display_name if focus else focus_name
        detail = _t("mdl.subtitle.manage", lang)
        subtitle = f"{name} · {detail}" if name else detail
        return (
            _t("mdl.heading.manage", lang),
            subtitle,
            "",
        )
    if all_set:
        name = identity(focus).display_name if focus else focus_name
        return (
            _t("mdl.heading.all_set", lang),
            _t("mdl.all_set.line", lang, app=name),
            _t("mdl.status.all_set", lang, app=name),
        )
    if explanation is not None and explanation.severity == "error":
        status = _t("mdl.status.stopped", lang)
    elif snapshot.paused:
        status = _t("mdl.status.paused", lang)
    elif snapshot.held:
        status = _t("mdl.status.cancelled", lang)
    elif explanation is not None and explanation.code is ErrorCode.MIRROR:
        status = _t("mdl.status.mirror", lang)
    elif (
        explanation is not None
        and explanation.code is ErrorCode.CHECKSUM
        and explanation.severity == "note"
    ):
        status = _t("mdl.status.checksum_again", lang)
    elif (active := _active_status(snapshot, lang)) is not None:
        status = active
    elif snapshot.note_code == "mirror":
        status = _t("mdl.status.mirror", lang)
    else:
        status = _t("mdl.status.waiting", lang)
    if focus:
        heading = _t("mdl.heading.preparing", lang, app=focus_name)
    else:
        heading = _t("mdl.heading.queue", lang)
    return heading, _t("mdl.subtitle.queue", lang), status


def _active_status(snapshot: Snapshot, lang: str) -> str | None:
    """Name the file that is downloading, or the one being checked if none is."""
    total = len(snapshot.items)
    for state, key in (
        (ItemState.DOWNLOADING, "mdl.status.downloading"),
        (ItemState.VERIFYING, "mdl.status.verifying"),
    ):
        for index, item in enumerate(snapshot.items, start=1):
            if item.state is not state:
                continue
            return _t(
                key,
                lang,
                index=index,
                total=total,
                name=localized_name(item.names, item.fallback, lang),
            )
    return None


def _active_explanation(snapshot: Snapshot, lang: str) -> Explanation | None:
    del lang
    if snapshot.boot_code:
        return _from_boot(snapshot)
    failed = next((item for item in snapshot.items if item.state is ItemState.FAILED), None)
    if failed is not None and failed.error_code:
        return _from_code(failed.error_code, failed.technical, failed)
    if snapshot.note_code == "mirror":
        what, do = ERROR_KEYS[ErrorCode.MIRROR]
        return Explanation(ErrorCode.MIRROR, what, do, {}, "", "note")
    if snapshot.note_code == "checksum":
        what, do = ERROR_KEYS[ErrorCode.CHECKSUM]
        return Explanation(ErrorCode.CHECKSUM, what, do, {}, "", "note")
    return None


def _from_boot(snapshot: Snapshot) -> Explanation:
    try:
        parsed = ErrorCode(snapshot.boot_code)
    except ValueError:
        parsed = ErrorCode.GENERIC
    what, do = ERROR_KEYS[parsed]
    return Explanation(parsed, what, do, dict(snapshot.boot_params), snapshot.boot_technical)


def _from_code(code: str, technical: str, item: QueueItem) -> Explanation:
    try:
        parsed = ErrorCode(code)
    except ValueError:
        parsed = ErrorCode.GENERIC
    what, do = ERROR_KEYS[parsed]
    params = dict(item.error_params)
    if parsed is ErrorCode.DISK and "need_bytes" not in params:
        params = _disk_params(technical)
    return Explanation(parsed, what, do, params, technical or item.technical)


def _disk_params(technical: str) -> dict[str, str]:
    return {"path": technical or "—", "need": "—", "free": "—"}


def _overall_eta(snapshot: Snapshot) -> float | None:
    speed = sum(item.speed_bps for item in snapshot.items if item.state is ItemState.DOWNLOADING)
    remaining = sum(
        max(0, item.size - item.received)
        for item in snapshot.items
        if item.state is not ItemState.READY
    )
    if speed <= 0 or remaining <= 0:
        return None
    return remaining / speed


def _sentence(explanation: Explanation, part: str, lang: str) -> str:
    key = explanation.what_key if part == "what" else explanation.do_key
    params = dict(explanation.params)
    params.pop("path", None)
    if "need_bytes" in params:
        params["need"] = format_size(_int(params.pop("need_bytes")), lang=lang)
        params["free"] = format_size(_int(params.pop("free_bytes")), lang=lang)
    params["folder"] = _t("mdl.disk.folder", lang)
    return _t(key, lang, **params)


def _error_path(explanation: Explanation | None) -> tuple[str, str]:
    if explanation is None:
        return "", ""
    technical = explanation.technical
    raw = str(explanation.params.get("path") or "")
    if not raw or raw == "—":
        return "", technical
    if raw not in technical:
        technical = f"{technical}\n{raw}".strip()
    return raw, technical


def _int(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _code_for(error: BaseException | str, technical: str) -> ErrorCode:
    if isinstance(error, DiskShort):
        return ErrorCode.DISK
    if isinstance(error, Cancelled):
        return ErrorCode.CANCELLED
    text = technical.lower()
    if "not enough disk space" in text or "no space" in text:
        return ErrorCode.DISK
    if "sha256" in text or "checksum" in text or "hash mismatch" in text:
        return ErrorCode.CHECKSUM
    if text.strip() in {"cancelled", "canceled"} or "cancelled" in text:
        return ErrorCode.CANCELLED
    if isinstance(error, (TimeoutError, ConnectionError, FetchError)) or _looks_like_network(text):
        return ErrorCode.NETWORK
    if isinstance(error, OSError) and "timed out" in text:
        return ErrorCode.NETWORK
    return ErrorCode.GENERIC


def _looks_like_network(text: str) -> bool:
    needles = (
        "timed out",
        "timeout",
        "network",
        "connection",
        "urlerror",
        "name or service",
        "all mirrors failed",
        "temporary failure",
        "unreachable",
    )
    return any(needle in text for needle in needles)


def _technical(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return str(error)
    return str(error)


def _active() -> str:
    return get_language()


def _t(key: str, lang: str | None, **params: object) -> str:
    if lang is None:
        return tr(key, **params)
    return tr_lang(key, lang, **params)
