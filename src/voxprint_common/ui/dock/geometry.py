"""Split rectangles, tab fitting, toolbar overflow, and drop zones.

Qt is not imported. Rectangles are logical points, origin at the top left.

Algorithms translated from craft-libs ``83bf4cb25c01ed46e7645c4172974239868aaf06``
(https://github.com/storytold/craft-libs), MIT OR Apache-2.0:

* ``crates/ui/src/layout/geometry.rs`` — one split and a divider move
* ``crates/ui/src/layout/resize.rs`` — arrow-key step and finite checks only
* ``crates/ui/src/layout/toolbar.rs`` — ``fit_toolbar``
* ``crates/ui/src/tabs/mod.rs`` — ``fit``
* ``crates/ui/src/docking/targets.rs`` — drop zone and preview rectangles

Copyright (c) 2026 ArtCraft Team and the craft-libs contributors.
VOXPrint distributes this translation under the Apache License, Version 2.0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

__all__ = [
    "MAX_EXTENT",
    "DropProposal",
    "DropTarget",
    "Rect",
    "SplitAxis",
    "SplitLayout",
    "SplitSize",
    "TabFit",
    "ToolbarFit",
    "Zone",
    "arrow_delta",
    "drop_zone",
    "fit_tabs",
    "fit_toolbar",
    "resolve_drop",
    "split_rect",
    "zone_rect",
]

MAX_EXTENT = 1_000_000.0
_MAX_GROUPS = 4096
_MAX_PANELS = 4096
_TAB_PASSES = 32


class SplitAxis(StrEnum):
    """Direction of a split. Horizontal places the first child on the left."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class SizeMode(StrEnum):
    """How a split stores the first child's size."""

    RATIO = "ratio"
    FIXED_FIRST = "fixed-first"
    FIXED_SECOND = "fixed-second"


class Zone(StrEnum):
    """Where a panel is inserted relative to an anchor."""

    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class SplitSize:
    """A ratio of the free span, or a fixed size in logical points."""

    mode: SizeMode
    value: float

    @staticmethod
    def ratio(value: float) -> SplitSize:
        """Fraction of the free span assigned to the first child."""
        return SplitSize(SizeMode.RATIO, value)

    @staticmethod
    def fixed_first(value: float) -> SplitSize:
        """First child size in logical points."""
        return SplitSize(SizeMode.FIXED_FIRST, value)

    @staticmethod
    def fixed_second(value: float) -> SplitSize:
        """Second child size in logical points."""
        return SplitSize(SizeMode.FIXED_SECOND, value)


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in logical points."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        """Right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """Bottom edge."""
        return self.y + self.height

    def contains(self, px: float, py: float) -> bool:
        """Return whether the point lies inside, edges included.

        Args:
            px: Horizontal position.
            py: Vertical position.
        """
        return self.x <= px <= self.right and self.y <= py <= self.bottom

    def center(self) -> tuple[float, float]:
        """Return the centre point."""
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True)
class SplitLayout:
    """Two panes and the divider between them."""

    first: Rect
    divider: Rect
    second: Rect
    first_size: float
    available: float

    def resized(self, size: SplitSize, delta: float, minimum: float) -> SplitSize | None:
        """Propose a size after moving the divider by ``delta`` points.

        The sizing mode is kept. When both minima cannot fit, each child
        receives half of the free span. Invalid input returns None so the
        caller can leave its stored size unchanged.

        Args:
            size: The size currently stored.
            delta: Movement along the split axis, in logical points.
            minimum: Smallest child, which may differ from the layout minimum.
        """
        if (
            not math.isfinite(size.value)
            or not math.isfinite(delta)
            or not _nonnegative(minimum)
            or self.available <= 0.0
        ):
            return None
        available = float(self.available)
        first = _clamp_first(float(self.first_size) + float(delta), available, minimum)
        if size.mode is SizeMode.RATIO:
            return SplitSize.ratio(first / available)
        if size.mode is SizeMode.FIXED_FIRST:
            return SplitSize.fixed_first(first)
        if size.mode is SizeMode.FIXED_SECOND:
            return SplitSize.fixed_second(available - first)
        assert_never(size.mode)


@dataclass(frozen=True)
class TabFit:
    """Tabs that fit on a strip, and tabs that need an overflow menu."""

    shown: tuple[tuple[int, float], ...]
    overflow: tuple[int, ...]
    overflow_width: float


@dataclass(frozen=True)
class ToolbarFit:
    """Visible toolbar groups and the groups that belong in an overflow menu."""

    shown: tuple[tuple[int, float], ...]
    overflow: tuple[int, ...]
    overflow_width: float


@dataclass(frozen=True)
class DropTarget[P]:
    """One visible group. Pass targets back to front."""

    anchor: P
    rect: Rect
    tab_strip: Rect | None
    tabs: tuple[tuple[P, Rect], ...]
    allow_splits: bool
    accepts_tabs: bool


@dataclass(frozen=True)
class DropProposal[P]:
    """A tab insertion or a side split, plus the preview rectangle."""

    anchor: P
    zone: Zone | None
    before: P | None
    preview: Rect
    insertion: Rect | None


def split_rect(
    rect: Rect,
    axis: SplitAxis,
    size: SplitSize,
    gap: float,
    minimum: float,
) -> SplitLayout | None:
    """Divide ``rect`` into two panes and a divider.

    ``gap`` and ``minimum`` are logical points. Equal pane minima relax to
    half the remaining span in a tiny area. Inverted rectangles, non-finite
    values, and a negative gap or minimum return None.

    Args:
        rect: The area to divide.
        axis: Horizontal or vertical.
        size: Requested first-child size. Values outside the area are clamped.
        gap: Divider thickness.
        minimum: Smallest child along the split axis.
    """
    if not _valid_rect(rect) or not _nonnegative(gap) or not _nonnegative(minimum):
        return None
    if not math.isfinite(size.value):
        return None
    total = rect.width if axis is SplitAxis.HORIZONTAL else rect.height
    used_gap = min(gap, total)
    available = total - used_gap
    space = float(available)
    if size.mode is SizeMode.RATIO:
        wanted = space * float(size.value)
    elif size.mode is SizeMode.FIXED_FIRST:
        wanted = float(size.value)
    elif size.mode is SizeMode.FIXED_SECOND:
        wanted = space - float(size.value)
    else:
        assert_never(size.mode)
    first_size = _clamp_first(wanted, space, minimum)
    if axis is SplitAxis.HORIZONTAL:
        first = Rect(rect.x, rect.y, min(first_size, rect.width), rect.height)
        divider_x = first.right
        divider_w = min(used_gap, rect.right - divider_x)
        divider = Rect(divider_x, rect.y, divider_w, rect.height)
        second = Rect(divider.right, rect.y, rect.right - divider.right, rect.height)
    elif axis is SplitAxis.VERTICAL:
        first = Rect(rect.x, rect.y, rect.width, min(first_size, rect.height))
        divider_y = first.bottom
        divider_h = min(used_gap, rect.bottom - divider_y)
        divider = Rect(rect.x, divider_y, rect.width, divider_h)
        second = Rect(rect.x, divider.bottom, rect.width, rect.bottom - divider.bottom)
    else:
        assert_never(axis)
    return SplitLayout(first, divider, second, first_size, available)


def arrow_delta(axis: SplitAxis, key: str, step: float = 8.0) -> float:
    """Return divider movement for an arrow key, in logical points.

    Left and up decrease the first child. Right and down increase it. Any
    other key, or a step that is not a positive finite number, returns 0.

    Args:
        axis: The split being resized.
        key: ``Left``, ``Right``, ``Up``, or ``Down``.
        step: Keyboard increment. The default is 8 points.
    """
    if not math.isfinite(step) or step <= 0.0:
        return 0.0
    if axis is SplitAxis.HORIZONTAL:
        if key == "Left":
            return -step
        if key == "Right":
            return step
        return 0.0
    if axis is SplitAxis.VERTICAL:
        if key == "Up":
            return -step
        if key == "Down":
            return step
        return 0.0
    assert_never(axis)


def fit_tabs(
    natural: Sequence[float],
    selected: int,
    available: float,
    minimum_width: float,
    overflow_width: float,
) -> TabFit:
    """Fit tab widths into ``available`` points.

    The widest tabs shrink first, down to ``minimum_width``. If they still
    do not fit, the selected tab stays and the rest move to an overflow list.
    Non-finite dimensions become zero. Finite dimensions are capped at one
    million points.

    Args:
        natural: Natural width of each tab, in order.
        selected: Index of the selected tab. An out-of-range index uses the last tab.
        available: Strip width.
        minimum_width: Floor used before tabs overflow.
        overflow_width: Reserved width of the overflow control.
    """
    room = _extent(available)
    floor_width = _extent(minimum_width)
    widths = tuple(_extent(width) for width in natural)
    indexes = tuple(range(len(widths)))
    if sum(float(width) for width in widths) <= float(room):
        return TabFit(tuple(zip(indexes, widths, strict=True)), (), 0.0)
    if len(widths) == 1:
        return TabFit(((0, room),), (), 0.0)
    if sum(min(width, floor_width) for width in widths) <= room:
        return TabFit(tuple(_squeeze(widths, indexes, room, floor_width)), (), 0.0)
    reserved = min(_extent(overflow_width), room)
    budget = room - reserved
    chosen = min(selected, max(len(widths) - 1, 0))
    keep = [False] * len(widths)
    used = 0.0
    order = (chosen, *(index for index in indexes if index != chosen))
    for index in order:
        width = min(widths[index], floor_width)
        if index == chosen or used + width <= budget:
            keep[index] = True
            used += width
    shown = tuple(index for index in indexes if keep[index])
    overflow = tuple(index for index in indexes if not keep[index])
    return TabFit(tuple(_squeeze(widths, shown, budget, floor_width)), overflow, reserved)


def fit_toolbar(
    natural: Sequence[float],
    available: float,
    spacing: float,
    overflow_width: float,
) -> ToolbarFit | None:
    """Fit indivisible toolbar groups, reserving a real overflow control.

    Groups keep their natural widths and their order. A wide group is not
    skipped so a later narrow group can show. None means the input is invalid
    or the row is shorter than the overflow control: the caller must use
    another presentation instead of hiding commands.

    Args:
        natural: Natural width of each group. Widths must be strictly positive.
        available: Row width.
        spacing: Gap between visible groups and before the overflow control.
        overflow_width: Full width of the overflow control. Zero is rejected.
    """
    if (
        len(natural) > _MAX_GROUPS
        or not _bounded(available)
        or not _bounded(spacing)
        or not _bounded(overflow_width)
        or overflow_width == 0.0
        or any(not _bounded(width) or width == 0.0 for width in natural)
    ):
        return None
    gaps = max(len(natural) - 1, 0)
    total = sum(float(width) for width in natural) + float(spacing) * gaps
    if total <= float(available):
        shown = tuple(enumerate(natural))
        return ToolbarFit(shown, (), 0.0)
    if available < overflow_width:
        return None
    budget = float(available) - float(overflow_width)
    used = 0.0
    shown_count = 0
    for width in natural:
        nxt = used + float(width) + float(spacing)
        if nxt <= budget:
            used = nxt
            shown_count += 1
        else:
            break
    shown = tuple(enumerate(natural[:shown_count]))
    overflow = tuple(range(shown_count, len(natural)))
    return ToolbarFit(shown, overflow, overflow_width)


def drop_zone(rect: Rect, tab_height: float, px: float, py: float) -> Zone | None:
    """Return the zone under a pointer.

    The tab header is the centre zone. Elsewhere the nearest quarter-edge
    wins. Outside, non-finite, empty, and inverted rectangles have no zone.

    Args:
        rect: Group rectangle.
        tab_height: Height of the tab header inside ``rect``.
        px: Pointer x.
        py: Pointer y.
    """
    if (
        not _usable(rect)
        or not math.isfinite(px)
        or not math.isfinite(py)
        or not rect.contains(px, py)
        or not math.isfinite(tab_height)
        or tab_height < 0.0
    ):
        return None
    if py < rect.y + min(tab_height, rect.height):
        return Zone.CENTER
    x = (px - rect.x) / rect.width
    y = (py - rect.y) / rect.height
    distances = (
        (x, Zone.LEFT),
        (1.0 - x, Zone.RIGHT),
        (y, Zone.TOP),
        (1.0 - y, Zone.BOTTOM),
    )
    distance, zone = min(distances, key=lambda item: item[0])
    if distance > 0.25:
        return Zone.CENTER
    return zone


def zone_rect(rect: Rect, zone: Zone) -> Rect | None:
    """Return the preview rectangle of a split or a group merge.

    Args:
        rect: Group rectangle.
        zone: Chosen zone.
    """
    if not _usable(rect):
        return None
    cx, cy = rect.center()
    if zone is Zone.CENTER:
        return rect
    if zone is Zone.LEFT:
        return Rect(rect.x, rect.y, cx - rect.x, rect.height)
    if zone is Zone.RIGHT:
        return Rect(cx, rect.y, rect.right - cx, rect.height)
    if zone is Zone.TOP:
        return Rect(rect.x, rect.y, rect.width, cy - rect.y)
    if zone is Zone.BOTTOM:
        return Rect(rect.x, cy, rect.width, rect.bottom - cy)
    assert_never(zone)


def resolve_drop[P](
    panel: P,
    px: float,
    py: float,
    targets: Sequence[DropTarget[P]],
) -> DropProposal[P] | None:
    """Resolve a pointer against visible groups.

    The frontmost containing group hides groups behind it, even when it
    rejects the drop. A tab strip reorders by midpoint. The body of the
    group that already holds ``panel`` is a no-op at the centre. Floating
    groups normally set ``allow_splits`` to false.

    Args:
        panel: The panel being dragged.
        px: Pointer x.
        py: Pointer y.
        targets: Groups from back to front.
    """
    if not math.isfinite(px) or not math.isfinite(py) or len(targets) > _MAX_PANELS:
        return None
    target = next(
        (item for item in reversed(targets) if _usable(item.rect) and item.rect.contains(px, py)),
        None,
    )
    if target is None or len(target.tabs) > _MAX_PANELS:
        return None
    anchor = target.anchor
    if anchor == panel:
        other = next((item for item, _rect in target.tabs if item != panel), None)
        if other is None:
            return None
        anchor = other
    strip = target.tab_strip
    if strip is not None and _usable(strip) and strip.contains(px, py):
        if not target.accepts_tabs:
            return None
        usable_tabs = tuple((item, rect) for item, rect in target.tabs if _usable(rect))
        before = next((item for item, rect in usable_tabs if px < rect.center()[0]), None)
        if before is not None:
            edge = next(rect.x for item, rect in usable_tabs if item == before)
        else:
            rights = [rect.right for _item, rect in usable_tabs]
            edge = min(max(rights, default=strip.x), strip.right)
        insertion = Rect(
            max(edge - 1.0, strip.x),
            strip.y,
            min(edge + 1.0, strip.right) - max(edge - 1.0, strip.x),
            strip.height,
        )
        return DropProposal(anchor, None, before, target.rect, insertion)
    zone = drop_zone(target.rect, 0.0, px, py) if target.allow_splits else Zone.CENTER
    if zone is None:
        return None
    if zone is Zone.CENTER:
        if not target.accepts_tabs or any(item == panel for item, _rect in target.tabs):
            return None
    elif not target.allow_splits:
        return None
    preview = zone_rect(target.rect, zone)
    if preview is None:
        return None
    return DropProposal(anchor, zone, None, preview, None)


def _extent(value: float) -> float:
    if math.isfinite(value):
        return min(max(value, 0.0), MAX_EXTENT)
    return 0.0


def _nonnegative(value: float) -> bool:
    return math.isfinite(value) and value >= 0.0


def _bounded(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= MAX_EXTENT


def _valid_rect(rect: Rect) -> bool:
    return (
        math.isfinite(rect.x)
        and math.isfinite(rect.y)
        and math.isfinite(rect.width)
        and math.isfinite(rect.height)
        and rect.width >= 0.0
        and rect.height >= 0.0
    )


def _usable(rect: Rect) -> bool:
    return (
        _valid_rect(rect)
        and rect.width > 0.0
        and rect.height > 0.0
        and rect.width <= MAX_EXTENT
        and rect.height <= MAX_EXTENT
        and abs(rect.x) <= MAX_EXTENT
        and abs(rect.y) <= MAX_EXTENT
    )


def _clamp_first(wanted: float, available: float, minimum: float) -> float:
    low = min(float(minimum), available * 0.5)
    return min(max(wanted, low), available - low)


def _squeeze(
    natural: Sequence[float],
    indexes: Sequence[int],
    total: float,
    minimum: float,
) -> list[tuple[int, float]]:
    def width_at(index: int) -> float:
        if 0 <= index < len(natural):
            return natural[index]
        return 0.0

    def sum_at(cap: float) -> float:
        total_width = 0.0
        for index in indexes:
            width = width_at(index)
            total_width += min(width, max(cap, min(width, minimum)))
        return total_width

    hi = minimum
    for index in indexes:
        hi = max(hi, width_at(index))
    lo = minimum
    if sum_at(hi) > total:
        for _ in range(_TAB_PASSES):
            mid = lo + (hi - lo) * 0.5
            if sum_at(mid) <= total:
                lo = mid
            else:
                hi = mid
    else:
        lo = hi
    remaining = total
    shown: list[tuple[int, float]] = []
    for index in indexes:
        width = width_at(index)
        fitted = min(width, max(lo, min(width, minimum)), remaining)
        remaining = max(remaining - fitted, 0.0)
        shown.append((index, fitted))
    return shown
