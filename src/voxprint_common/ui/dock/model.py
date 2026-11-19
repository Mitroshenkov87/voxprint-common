"""Transactional dock layout.

A layout is a tree of tab groups, splits, and accordion stacks, plus floating
groups that stay inside one window. :meth:`Layout.apply` clones, checks, and
commits only a valid tree. :class:`DockHistory` records those commits for
undo and redo. :func:`dump_layout` writes schema 1 JSON.

Translated from craft-libs ``crates/ui/src/docking/model.rs`` at
``83bf4cb25c01ed46e7645c4172974239868aaf06``
(https://github.com/storytold/craft-libs), MIT OR Apache-2.0.
Copyright (c) 2026 ArtCraft Team and the craft-libs contributors.
VOXPrint distributes this translation under the Apache License, Version 2.0.
The in-memory undo stack and the versioned JSON document are VOXPrint additions.

Qt is not imported. Panel ids are hashable values chosen by the app. JSON
uses strings.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar, assert_never

from voxprint_common.ui.dock.geometry import (
    MAX_EXTENT,
    SizeMode,
    SplitAxis,
    SplitSize,
    Zone,
)

__all__ = [
    "LAYOUT_SCHEMA",
    "MAX_DEPTH",
    "MAX_PANELS",
    "Action",
    "Activate",
    "Close",
    "DockError",
    "DockErrorKind",
    "DockHistory",
    "Float",
    "Floating",
    "Layout",
    "LayoutFormatError",
    "Location",
    "Move",
    "MoveFloating",
    "Open",
    "OpenAt",
    "Permissions",
    "Placement",
    "ResizeSplit",
    "ResizeStack",
    "SetStackOpen",
    "Split",
    "SplitPlacement",
    "Stack",
    "StackEntry",
    "TabPlacement",
    "Tabs",
    "dump_layout",
    "layout_from_json",
    "layout_json",
    "load_layout",
]

MAX_DEPTH = 64
MAX_PANELS = 4096
_MAX_NODES = MAX_PANELS * 2
LAYOUT_SCHEMA = 1

P = TypeVar("P", bound=Hashable)


class DockErrorKind(StrEnum):
    """Why a dock transaction was refused."""

    LIMIT_EXCEEDED = "limit-exceeded"
    DUPLICATE_PANEL = "duplicate-panel"
    MISSING_PANEL = "missing-panel"
    MISSING_TARGET = "missing-target"
    INVALID_SELECTION = "invalid-selection"
    INVALID_GEOMETRY = "invalid-geometry"
    INVALID_PLACEMENT = "invalid-placement"
    NOT_ALLOWED = "not-allowed"
    EMPTY_HISTORY = "empty-history"


class DockError(Exception):
    """A dock transaction was refused. The layout is unchanged."""

    def __init__(self, kind: DockErrorKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class LayoutFormatError(ValueError):
    """A saved layout is not schema 1 or is not a layout object."""


@dataclass(frozen=True)
class Permissions:
    """What one panel allows. The centre canvas uses :meth:`protected`."""

    movable: bool = True
    floatable: bool = True
    closable: bool = True
    accepts_tabs: bool = True

    @classmethod
    def protected(cls) -> Permissions:
        """Block move, float, close, and tab merges. Side splits stay allowed."""
        return cls(movable=False, floatable=False, closable=False, accepts_tabs=False)


@dataclass(frozen=True)
class TabPlacement[P: Hashable]:
    """Insert before an existing member, or append when ``before`` is None."""

    before: P | None


@dataclass(frozen=True)
class SplitPlacement:
    """Insert beside an anchor. Centre inserts into its tab group."""

    zone: Zone


Placement = TabPlacement[P] | SplitPlacement


@dataclass
class StackEntry[P: Hashable]:
    """One accordion row."""

    panel: P
    open: bool
    height: float | None


@dataclass
class Tabs[P: Hashable]:
    """Ordered panel ids and the active index."""

    panels: list[P]
    active: int


@dataclass
class Stack[P: Hashable]:
    """Accordion rows. Open state and height survive a reorder."""

    entries: list[StackEntry[P]]


@dataclass
class Split[P: Hashable]:
    """Two children. ``minimum`` is the smallest child along the axis."""

    axis: SplitAxis
    size: SplitSize
    first: Node[P]
    second: Node[P]
    minimum: float = 0.0


Node = Split[P] | Tabs[P] | Stack[P]


@dataclass
class Floating[P: Hashable]:
    """A tab group drawn inside the window, not a second top-level window."""

    panels: list[P]
    active: int
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class Move[P: Hashable]:
    """Move a panel next to an anchor."""

    panel: P
    anchor: P
    placement: Placement[P]


@dataclass(frozen=True)
class Float[P: Hashable]:
    """Detach a panel into a floating group."""

    panel: P
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class Close[P: Hashable]:
    """Remove a panel. Empty groups collapse."""

    panel: P


@dataclass(frozen=True)
class Activate[P: Hashable]:
    """Select a panel in its group, or open its accordion row."""

    panel: P


@dataclass(frozen=True)
class Open[P: Hashable]:
    """Reveal an absent panel in an anchor's group, or select it if present."""

    panel: P
    anchor: P | None = None


@dataclass(frozen=True)
class OpenAt[P: Hashable]:
    """Reveal an absent panel at an explicit destination."""

    panel: P
    anchor: P
    placement: Placement[P]


@dataclass(frozen=True)
class MoveFloating[P: Hashable]:
    """Move the floating group that contains ``panel``."""

    panel: P
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class ResizeSplit:
    """Set the size of the split reached by ``path``.

    False walks to the first child and true walks to the second.
    """

    path: tuple[bool, ...]
    size: SplitSize


@dataclass(frozen=True)
class SetStackOpen[P: Hashable]:
    """Open or collapse one accordion row."""

    panel: P
    open: bool


@dataclass(frozen=True)
class ResizeStack[P: Hashable]:
    """Set one accordion row's preferred height. None clears it."""

    panel: P
    height: float | None


Action = (
    Move[P]
    | Float[P]
    | Close[P]
    | Activate[P]
    | Open[P]
    | OpenAt[P]
    | MoveFloating[P]
    | ResizeSplit
    | SetStackOpen[P]
    | ResizeStack[P]
)


@dataclass
class Location[P: Hashable]:
    """Where a closed panel should return."""

    floating: tuple[float, float, float, float] | None
    anchor: P | None
    placement: Placement[P]
    size: SplitSize | None
    stack: tuple[bool, float | None] | None
    sibling: list[P] = field(default_factory=list)


@dataclass
class Layout[P: Hashable]:
    """The dock tree and its floating groups."""

    root: Node[P] | None = None
    floating: list[Floating[P]] = field(default_factory=list)

    def validate(self) -> None:
        """Check depth, uniqueness, selection, and geometry.

        Raises:
            DockError: The tree is not safe to commit.
        """
        if len(self.floating) > MAX_PANELS:
            raise DockError(DockErrorKind.LIMIT_EXCEEDED)
        seen: set[P] = set()
        count = 0
        visit: list[tuple[Node[P], int]] = []
        if self.root is not None:
            visit.append((self.root, 1))
        while visit:
            node, depth = visit.pop()
            count += 1
            if depth > MAX_DEPTH or count > _MAX_NODES:
                raise DockError(DockErrorKind.LIMIT_EXCEEDED)
            if isinstance(node, Split):
                if not _valid_size(node.size) or not _valid_minimum(node.minimum):
                    raise DockError(DockErrorKind.INVALID_GEOMETRY)
                visit.append((node.second, depth + 1))
                visit.append((node.first, depth + 1))
            elif isinstance(node, Tabs):
                _check_tabs(node.panels, node.active, seen)
            elif isinstance(node, Stack):
                if len(node.entries) > MAX_PANELS:
                    raise DockError(DockErrorKind.LIMIT_EXCEEDED)
                for entry in node.entries:
                    if not _valid_height(entry.height):
                        raise DockError(DockErrorKind.INVALID_GEOMETRY)
                    _check_panel(entry.panel, seen)
            else:
                assert_never(node)
        for group in self.floating:
            if not _valid_rect(group.rect) or not group.panels:
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            _check_tabs(group.panels, group.active, seen)

    def panels(self) -> list[P]:
        """Return panel ids. Traversal stops at the size limit."""
        found: list[P] = []
        visit: list[Node[P]] = []
        if self.root is not None:
            visit.append(self.root)
        visited = 0
        while visit:
            node = visit.pop()
            visited += 1
            if visited > _MAX_NODES or len(found) >= MAX_PANELS:
                break
            if isinstance(node, Split):
                visit.append(node.second)
                visit.append(node.first)
            elif isinstance(node, Tabs):
                room = MAX_PANELS - len(found)
                found.extend(node.panels[:room])
            elif isinstance(node, Stack):
                room = MAX_PANELS - len(found)
                found.extend(entry.panel for entry in node.entries[:room])
            else:
                assert_never(node)
        for group in self.floating[:MAX_PANELS]:
            room = MAX_PANELS - len(found)
            if room <= 0:
                break
            found.extend(group.panels[:room])
        return found

    def contains(self, panel: P) -> bool:
        """Return whether ``panel`` is in the tree or a floating group.

        Args:
            panel: Panel id.
        """
        return panel in self.panels()

    def location(self, panel: P) -> Location[P]:
        """Capture where ``panel`` sits, so a later restore can put it back.

        Args:
            panel: A panel that is currently present.

        Raises:
            DockError: The layout is invalid or the panel is absent.
        """
        self.validate()
        for group in self.floating:
            if panel in group.panels:
                located = _group_location(group.panels, panel)
                if located is None:
                    raise DockError(DockErrorKind.MISSING_PANEL)
                located.floating = group.rect
                return located
        if self.root is not None:
            located = _node_location(self.root, panel)
            if located is not None:
                return located
        raise DockError(DockErrorKind.MISSING_PANEL)

    def restore(self, panel: P, location: Location[P]) -> None:
        """Put an absent panel back at ``location``.

        A missing anchor leaves this layout unchanged and raises
        :attr:`DockErrorKind.MISSING_TARGET`.

        Args:
            panel: A panel that is not in the layout.
            location: A location captured earlier for this panel.

        Raises:
            DockError: The layout or the location cannot be committed.
        """
        self.validate()
        if self.contains(panel):
            raise DockError(DockErrorKind.DUPLICATE_PANEL)
        if location.size is not None and not _valid_size(location.size):
            raise DockError(DockErrorKind.INVALID_GEOMETRY)
        if location.stack is not None and not _valid_height(location.stack[1]):
            raise DockError(DockErrorKind.INVALID_GEOMETRY)
        proposed = copy.deepcopy(self)
        if location.floating is not None:
            if not _valid_rect(location.floating):
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            if location.anchor is not None:
                if not any(location.anchor in group.panels for group in proposed.floating):
                    raise DockError(DockErrorKind.MISSING_TARGET)
                proposed._apply_inner(OpenAt(panel, location.anchor, location.placement))
            else:
                proposed.floating.append(Floating([panel], 0, location.floating))
        elif location.anchor is not None:
            if not proposed.contains(location.anchor):
                raise DockError(DockErrorKind.MISSING_TARGET)
            side = (
                isinstance(location.placement, SplitPlacement)
                and location.placement.zone in {Zone.LEFT, Zone.RIGHT, Zone.TOP, Zone.BOTTOM}
                and bool(location.sibling)
            )
            if side and isinstance(location.placement, SplitPlacement):
                if len(location.sibling) > MAX_PANELS:
                    raise DockError(DockErrorKind.LIMIT_EXCEEDED)
                members = set(location.sibling)
                if len(members) != len(location.sibling) or location.anchor not in members:
                    raise DockError(DockErrorKind.INVALID_PLACEMENT)
                if proposed.root is None:
                    raise DockError(DockErrorKind.MISSING_TARGET)
                placed, root = _restore_around(
                    proposed.root,
                    panel,
                    members,
                    location.placement.zone,
                    location.size or SplitSize.ratio(0.5),
                )
                if not placed or root is None:
                    raise DockError(DockErrorKind.MISSING_TARGET)
                proposed.root = root
            else:
                proposed._apply_inner(OpenAt(panel, location.anchor, location.placement))
                if location.size is not None and proposed.root is not None:
                    _restore_parent_size(proposed.root, panel, location.size)
        elif proposed.root is None:
            proposed.root = Tabs([panel], 0)
        else:
            raise DockError(DockErrorKind.MISSING_TARGET)
        if location.stack is not None and proposed.root is not None:
            proposed.root = _restore_stack_kind(proposed.root, panel)
            entry = _stack_entry(proposed.root, panel)
            if entry is not None:
                entry.open = location.stack[0]
                entry.height = location.stack[1]
        proposed.validate()
        self.root = proposed.root
        self.floating = proposed.floating

    def apply(
        self,
        action: Action[P],
        permissions: Callable[[P], Permissions] | None = None,
    ) -> None:
        """Commit ``action`` or leave this layout unchanged.

        Args:
            action: One dock transaction.
            permissions: Policy for each panel. The default allows every operation.

        Raises:
            DockError: The action is not allowed or would produce an invalid tree.
        """
        policy = permissions or (lambda _panel: Permissions())
        self.validate()
        _check_permissions(action, policy)
        proposed = copy.deepcopy(self)
        proposed._apply_inner(action)
        proposed.validate()
        self.root = proposed.root
        self.floating = proposed.floating

    def _apply_inner(self, action: Action[P]) -> None:
        if isinstance(action, Move):
            self._move(action)
        elif isinstance(action, Float):
            if not _valid_rect(action.rect):
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            entry = self._take(action.panel)
            if entry is None:
                raise DockError(DockErrorKind.MISSING_PANEL)
            self.floating.append(Floating([entry.panel], 0, action.rect))
        elif isinstance(action, Close):
            if self._take(action.panel) is None:
                raise DockError(DockErrorKind.MISSING_PANEL)
        elif isinstance(action, Activate):
            if self.root is not None and _activate(self.root, action.panel):
                return
            for group in self.floating:
                if action.panel in group.panels:
                    group.active = group.panels.index(action.panel)
                    return
            raise DockError(DockErrorKind.MISSING_PANEL)
        elif isinstance(action, Open):
            if self.contains(action.panel):
                self._apply_inner(Activate(action.panel))
                return
            entry = StackEntry(action.panel, True, None)
            if action.anchor is not None:
                self._insert(entry, action.anchor, TabPlacement(None))
            elif self.root is None:
                self.root = Tabs([action.panel], 0)
            else:
                raise DockError(DockErrorKind.MISSING_TARGET)
        elif isinstance(action, OpenAt):
            if self.contains(action.panel):
                self._apply_inner(Move(action.panel, action.anchor, action.placement))
                return
            if (
                isinstance(action.placement, TabPlacement)
                and action.placement.before is not None
                and not self._same_group(action.placement.before, action.anchor)
            ):
                raise DockError(DockErrorKind.MISSING_TARGET)
            self._insert(StackEntry(action.panel, True, None), action.anchor, action.placement)
        elif isinstance(action, MoveFloating):
            if not _valid_rect(action.rect):
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            group = next((item for item in self.floating if action.panel in item.panels), None)
            if group is None:
                raise DockError(DockErrorKind.MISSING_PANEL)
            group.rect = action.rect
        elif isinstance(action, ResizeSplit):
            if len(action.path) >= MAX_DEPTH:
                raise DockError(DockErrorKind.LIMIT_EXCEEDED)
            if not _valid_size(action.size):
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            node = self.root
            if node is None:
                raise DockError(DockErrorKind.MISSING_TARGET)
            for second_child in action.path:
                if not isinstance(node, Split):
                    raise DockError(DockErrorKind.MISSING_TARGET)
                node = node.second if second_child else node.first
            if not isinstance(node, Split):
                raise DockError(DockErrorKind.MISSING_TARGET)
            node.size = action.size
        elif isinstance(action, SetStackOpen):
            entry = _stack_entry(self.root, action.panel) if self.root is not None else None
            if entry is None:
                raise DockError(DockErrorKind.MISSING_PANEL)
            entry.open = action.open
        elif isinstance(action, ResizeStack):
            if not _valid_height(action.height):
                raise DockError(DockErrorKind.INVALID_GEOMETRY)
            entry = _stack_entry(self.root, action.panel) if self.root is not None else None
            if entry is None:
                raise DockError(DockErrorKind.MISSING_PANEL)
            entry.height = action.height
        else:
            assert_never(action)

    def _move(self, action: Move[P]) -> None:
        if action.panel == action.anchor:
            raise DockError(DockErrorKind.INVALID_PLACEMENT)
        if not self.contains(action.anchor):
            raise DockError(DockErrorKind.MISSING_TARGET)
        if isinstance(action.placement, TabPlacement) and action.placement.before is not None:
            if action.placement.before == action.panel:
                if self._same_group(action.panel, action.anchor):
                    return
                raise DockError(DockErrorKind.INVALID_PLACEMENT)
            if not self._same_group(action.placement.before, action.anchor):
                raise DockError(DockErrorKind.MISSING_TARGET)
        entry = self._take(action.panel)
        if entry is None:
            raise DockError(DockErrorKind.MISSING_PANEL)
        self._insert(entry, action.anchor, action.placement)

    def _same_group(self, panel: P, anchor: P) -> bool:
        if self.root is not None and _same_in(self.root, panel, anchor):
            return True
        return any(panel in group.panels and anchor in group.panels for group in self.floating)

    def _take(self, panel: P) -> StackEntry[P] | None:
        removed: StackEntry[P] | None = None
        if self.root is not None:
            self.root, removed = _remove(self.root, panel)
        if removed is None:
            for group in self.floating:
                active = [group.active]
                taken = _remove_tab(group.panels, active, panel)
                group.active = active[0]
                if taken is not None:
                    removed = StackEntry(taken, True, None)
                    break
            self.floating = [group for group in self.floating if group.panels]
        return removed

    def _insert(self, entry: StackEntry[P], anchor: P, placement: Placement[P]) -> None:
        for group in self.floating:
            if anchor not in group.panels:
                continue
            if isinstance(placement, SplitPlacement) and placement.zone is not Zone.CENTER:
                raise DockError(DockErrorKind.INVALID_PLACEMENT)
            index = _member_index(group.panels, placement, anchor)
            group.panels.insert(index, entry.panel)
            group.active = index
            return
        if self.root is not None:
            inserted = _insert_node(self.root, entry, anchor, placement)
            if inserted is not None:
                self.root = inserted
                return
        raise DockError(DockErrorKind.MISSING_TARGET)


class DockHistory[P: Hashable]:
    """Undo and redo around :meth:`Layout.apply`.

    Each successful apply stores the previous tree. Undo and redo swap those
    trees. A failed apply does not move the stacks.
    """

    def __init__(self, layout: Layout[P]) -> None:
        self.layout = layout
        self._undo: list[Layout[P]] = []
        self._redo: list[Layout[P]] = []

    def apply(
        self,
        action: Action[P],
        permissions: Callable[[P], Permissions] | None = None,
    ) -> None:
        """Apply ``action`` and remember the previous layout.

        Args:
            action: One dock transaction.
            permissions: Passed through to :meth:`Layout.apply`.

        Raises:
            DockError: The action was refused. The history is unchanged.
        """
        snapshot = copy.deepcopy(self.layout)
        self.layout.apply(action, permissions)
        self._undo.append(snapshot)
        self._redo.clear()

    def undo(self) -> None:
        """Restore the layout from before the latest successful apply.

        Raises:
            DockError: Nothing has been applied.
        """
        if not self._undo:
            raise DockError(DockErrorKind.EMPTY_HISTORY)
        self._redo.append(copy.deepcopy(self.layout))
        self.layout = self._undo.pop()

    def redo(self) -> None:
        """Reapply the layout that :meth:`undo` stepped away from.

        Raises:
            DockError: There is no undone apply.
        """
        if not self._redo:
            raise DockError(DockErrorKind.EMPTY_HISTORY)
        self._undo.append(copy.deepcopy(self.layout))
        self.layout = self._redo.pop()

    @property
    def can_undo(self) -> bool:
        """Whether :meth:`undo` has a layout to restore."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """Whether :meth:`redo` has a layout to restore."""
        return bool(self._redo)


def dump_layout(layout: Layout[str]) -> dict[str, object]:
    """Return schema 1 JSON for a string-id layout.

    Args:
        layout: A valid layout whose panel ids are strings.

    Raises:
        DockError: ``layout`` does not validate.
    """
    layout.validate()
    return {
        "schema": LAYOUT_SCHEMA,
        "root": _dump_node(layout.root),
        "floating": _dump_floating(layout.floating),
    }


def load_layout(payload: object) -> Layout[str]:
    """Read a schema 1 layout and validate it.

    Args:
        payload: An object previously returned by :func:`dump_layout`.

    Raises:
        LayoutFormatError: The schema or the shape is wrong.
        DockError: The tree parses but is not a valid layout.
    """
    if not isinstance(payload, dict) or payload.get("schema") != LAYOUT_SCHEMA:
        raise LayoutFormatError("dock layout schema must be 1")
    floating = payload.get("floating")
    if not isinstance(floating, list):
        raise LayoutFormatError("dock layout floating must be a list")
    layout = Layout(_load_node(payload.get("root")), [_load_floating(item) for item in floating])
    layout.validate()
    return layout


def layout_json(layout: Layout[str]) -> str:
    """Serialise ``layout`` as UTF-8 JSON text.

    Args:
        layout: A valid string-id layout.
    """
    return json.dumps(
        dump_layout(layout), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def layout_from_json(text: str) -> Layout[str]:
    """Parse :func:`layout_json` text.

    Args:
        text: Schema 1 JSON.

    Raises:
        LayoutFormatError: The text is not a JSON object of schema 1.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LayoutFormatError("dock layout is not JSON") from exc
    return load_layout(payload)


def _check_permissions[P: Hashable](
    action: Action[P], permissions: Callable[[P], Permissions]
) -> None:
    if isinstance(action, Move | OpenAt):
        if not permissions(action.panel).movable:
            raise DockError(DockErrorKind.NOT_ALLOWED)
        merging = isinstance(action.placement, TabPlacement) or (
            isinstance(action.placement, SplitPlacement) and action.placement.zone is Zone.CENTER
        )
        if merging and not permissions(action.anchor).accepts_tabs:
            raise DockError(DockErrorKind.NOT_ALLOWED)
    elif isinstance(action, Float):
        allowed = permissions(action.panel)
        if not allowed.floatable or not allowed.movable:
            raise DockError(DockErrorKind.NOT_ALLOWED)
    elif isinstance(action, Close):
        if not permissions(action.panel).closable:
            raise DockError(DockErrorKind.NOT_ALLOWED)
    elif isinstance(action, MoveFloating):
        if not permissions(action.panel).movable:
            raise DockError(DockErrorKind.NOT_ALLOWED)
    elif isinstance(action, Open):
        if action.anchor is not None and not permissions(action.anchor).accepts_tabs:
            raise DockError(DockErrorKind.NOT_ALLOWED)
    elif isinstance(action, Activate | ResizeSplit | SetStackOpen | ResizeStack):
        return
    else:
        assert_never(action)


def _valid_rect(rect: tuple[float, float, float, float]) -> bool:
    if len(rect) != 4:
        return False
    return (
        all(math.isfinite(value) and abs(value) <= MAX_EXTENT for value in rect)
        and rect[2] > 0
        and rect[3] > 0
    )


def _valid_size(size: SplitSize) -> bool:
    if size.mode is SizeMode.RATIO:
        return math.isfinite(size.value) and 0.0 <= size.value <= 1.0
    if size.mode in {SizeMode.FIXED_FIRST, SizeMode.FIXED_SECOND}:
        return math.isfinite(size.value) and 0.0 <= size.value <= MAX_EXTENT
    assert_never(size.mode)


def _valid_minimum(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= MAX_EXTENT


def _valid_height(height: float | None) -> bool:
    return height is None or (math.isfinite(height) and 0.0 <= height <= MAX_EXTENT)


def _valid_active(length: int, active: int) -> bool:
    return active < length or (length == 0 and active == 0)


def _check_panel[P: Hashable](panel: P, seen: set[P]) -> None:
    if len(seen) >= MAX_PANELS:
        raise DockError(DockErrorKind.LIMIT_EXCEEDED)
    if panel in seen:
        raise DockError(DockErrorKind.DUPLICATE_PANEL)
    seen.add(panel)


def _check_tabs[P: Hashable](panels: list[P], active: int, seen: set[P]) -> None:
    if len(panels) > MAX_PANELS:
        raise DockError(DockErrorKind.LIMIT_EXCEEDED)
    if not _valid_active(len(panels), active):
        raise DockError(DockErrorKind.INVALID_SELECTION)
    for panel in panels:
        _check_panel(panel, seen)


def _remove_tab[P: Hashable](panels: list[P], active: list[int], panel: P) -> P | None:
    if panel not in panels:
        return None
    index = panels.index(panel)
    selected = panels[active[0]] if 0 <= active[0] < len(panels) else None
    removed = panels.pop(index)
    if selected in panels:
        active[0] = panels.index(selected)
    else:
        active[0] = min(index, max(len(panels) - 1, 0))
    return removed


def _remove[P: Hashable](node: Node[P], panel: P) -> tuple[Node[P] | None, StackEntry[P] | None]:
    if isinstance(node, Tabs):
        panels = list(node.panels)
        active = [node.active]
        removed = _remove_tab(panels, active, panel)
        if removed is None:
            return node, None
        entry = StackEntry(removed, True, None)
        if not panels:
            return None, entry
        return Tabs(panels, active[0]), entry
    if isinstance(node, Stack):
        index = next((i for i, entry in enumerate(node.entries) if entry.panel == panel), None)
        if index is None:
            return node, None
        entries = list(node.entries)
        entry = entries.pop(index)
        if not entries:
            return None, entry
        return Stack(entries), entry
    if isinstance(node, Split):
        first, left = _remove(node.first, panel)
        second, right = _remove(node.second, panel)
        removed = left if left is not None else right
        if removed is None:
            return node, None
        if first is None:
            return second, removed
        if second is None:
            return first, removed
        return Split(node.axis, node.size, first, second, node.minimum), removed
    assert_never(node)


def _activate[P: Hashable](node: Node[P], panel: P) -> bool:
    if isinstance(node, Tabs):
        if panel not in node.panels:
            return False
        node.active = node.panels.index(panel)
        return True
    if isinstance(node, Stack):
        entry = next((item for item in node.entries if item.panel == panel), None)
        if entry is None:
            return False
        entry.open = True
        return True
    if isinstance(node, Split):
        return _activate(node.first, panel) or _activate(node.second, panel)
    assert_never(node)


def _stack_entry[P: Hashable](node: Node[P] | None, panel: P) -> StackEntry[P] | None:
    if node is None:
        return None
    if isinstance(node, Stack):
        return next((entry for entry in node.entries if entry.panel == panel), None)
    if isinstance(node, Split):
        return _stack_entry(node.first, panel) or _stack_entry(node.second, panel)
    if isinstance(node, Tabs):
        return None
    assert_never(node)


def _same_in[P: Hashable](node: Node[P], panel: P, anchor: P) -> bool:
    if isinstance(node, Tabs):
        return panel in node.panels and anchor in node.panels
    if isinstance(node, Stack):
        ids = [entry.panel for entry in node.entries]
        return panel in ids and anchor in ids
    if isinstance(node, Split):
        return _same_in(node.first, panel, anchor) or _same_in(node.second, panel, anchor)
    assert_never(node)


def _member_index[P: Hashable](panels: list[P], placement: Placement[P], anchor: P) -> int:
    if isinstance(placement, TabPlacement):
        if placement.before is None or placement.before not in panels:
            return len(panels)
        return panels.index(placement.before)
    if isinstance(placement, SplitPlacement):
        if anchor not in panels:
            return len(panels)
        return panels.index(anchor) + 1
    assert_never(placement)


def _insert_node[P: Hashable](
    node: Node[P], entry: StackEntry[P], anchor: P, placement: Placement[P]
) -> Node[P] | None:
    if isinstance(node, Split):
        first = _insert_node(node.first, entry, anchor, placement)
        if first is not None:
            node.first = first
            return node
        second = _insert_node(node.second, entry, anchor, placement)
        if second is not None:
            node.second = second
            return node
        return None
    if isinstance(node, Tabs):
        here = anchor in node.panels
    elif isinstance(node, Stack):
        here = any(item.panel == anchor for item in node.entries)
    else:
        assert_never(node)
    if not here:
        return None
    if isinstance(placement, SplitPlacement) and placement.zone in {
        Zone.LEFT,
        Zone.RIGHT,
        Zone.TOP,
        Zone.BOTTOM,
    }:
        return _split_around(node, entry, placement.zone)
    if isinstance(node, Tabs):
        panels = list(node.panels)
        index = _member_index(panels, placement, anchor)
        panels.insert(index, entry.panel)
        return Tabs(panels, index)
    entries = list(node.entries)
    index = _member_index([item.panel for item in entries], placement, anchor)
    entries.insert(index, entry)
    return Stack(entries)


def _split_around[P: Hashable](old: Node[P], entry: StackEntry[P], zone: Zone) -> Split[P]:
    created = Tabs([entry.panel], 0)
    vertical = zone in {Zone.TOP, Zone.BOTTOM}
    axis = SplitAxis.VERTICAL if vertical else SplitAxis.HORIZONTAL
    if zone in {Zone.TOP, Zone.LEFT}:
        first: Node[P] = created
        second: Node[P] = old
    else:
        first = old
        second = created
    return Split(axis, SplitSize.ratio(0.5), first, second, 0.0)


def _group_location[P: Hashable](panels: list[P], panel: P) -> Location[P] | None:
    if panel not in panels:
        return None
    index = panels.index(panel)
    anchor = next((item for item in panels if item != panel), None)
    before = panels[index + 1] if index + 1 < len(panels) else None
    return Location(None, anchor, TabPlacement(before), None, None, [])


def _first_panel[P: Hashable](node: Node[P]) -> P | None:
    if isinstance(node, Tabs):
        return node.panels[0] if node.panels else None
    if isinstance(node, Stack):
        return node.entries[0].panel if node.entries else None
    if isinstance(node, Split):
        return _first_panel(node.first) or _first_panel(node.second)
    assert_never(node)


def _members[P: Hashable](node: Node[P], found: list[P]) -> None:
    if isinstance(node, Tabs):
        found.extend(node.panels)
    elif isinstance(node, Stack):
        found.extend(entry.panel for entry in node.entries)
    elif isinstance(node, Split):
        _members(node.first, found)
        _members(node.second, found)
    else:
        assert_never(node)


def _node_location[P: Hashable](node: Node[P], panel: P) -> Location[P] | None:
    if isinstance(node, Tabs):
        return _group_location(node.panels, panel)
    if isinstance(node, Stack):
        entry = next((item for item in node.entries if item.panel == panel), None)
        if entry is None:
            return None
        located = _group_location([item.panel for item in node.entries], panel)
        if located is None:
            return None
        located.stack = (entry.open, entry.height)
        return located
    if isinstance(node, Split):
        located = _node_location(node.first, panel)
        sibling: Node[P] | None = node.second if located is not None else None
        is_first = located is not None
        if located is None:
            located = _node_location(node.second, panel)
            sibling = node.first
        if located is None or sibling is None:
            return None
        if located.anchor is None:
            anchor = _first_panel(sibling)
            if anchor is not None:
                located.anchor = anchor
                if node.axis is SplitAxis.HORIZONTAL:
                    zone = Zone.LEFT if is_first else Zone.RIGHT
                elif node.axis is SplitAxis.VERTICAL:
                    zone = Zone.TOP if is_first else Zone.BOTTOM
                else:
                    assert_never(node.axis)
                located.placement = SplitPlacement(zone)
                located.size = node.size
                members: list[P] = []
                _members(sibling, members)
                located.sibling = members
        return located
    assert_never(node)


def _restore_parent_size[P: Hashable](node: Node[P], panel: P, size: SplitSize) -> bool:
    if not isinstance(node, Split):
        return False
    if _alone(node.first, panel) or _alone(node.second, panel):
        node.size = size
        return True
    return _restore_parent_size(node.first, panel, size) or _restore_parent_size(
        node.second, panel, size
    )


def _alone[P: Hashable](node: Node[P], panel: P) -> bool:
    return isinstance(node, Tabs) and node.panels == [panel]


def _restore_stack_kind[P: Hashable](node: Node[P], panel: P) -> Node[P]:
    if isinstance(node, Tabs) and node.panels == [panel]:
        return Stack([StackEntry(panel, True, None)])
    if isinstance(node, Split):
        node.first = _restore_stack_kind(node.first, panel)
        node.second = _restore_stack_kind(node.second, panel)
    return node


def _restore_around[P: Hashable](
    node: Node[P],
    panel: P,
    members: set[P],
    zone: Zone,
    size: SplitSize,
) -> tuple[bool, Node[P]]:
    actual: list[P] = []
    _members(node, actual)
    if len(actual) == len(members) and all(item in members for item in actual):
        created: Node[P] = Tabs([panel], 0)
        vertical = zone in {Zone.TOP, Zone.BOTTOM}
        axis = SplitAxis.VERTICAL if vertical else SplitAxis.HORIZONTAL
        if zone in {Zone.LEFT, Zone.TOP}:
            first: Node[P] = created
            second: Node[P] = node
        else:
            first = node
            second = created
        return True, Split(axis, size, first, second, 0.0)
    if isinstance(node, Split):
        placed, first = _restore_around(node.first, panel, members, zone, size)
        if placed:
            node.first = first
            return True, node
        placed, second = _restore_around(node.second, panel, members, zone, size)
        if placed:
            node.second = second
            return True, node
    return False, node


def _dump_node(node: Node[str] | None) -> object:
    if node is None:
        return None
    if isinstance(node, Split):
        return {
            "type": "split",
            "axis": node.axis.value,
            "size": {"mode": node.size.mode.value, "value": node.size.value},
            "minimum": node.minimum,
            "first": _dump_node(node.first),
            "second": _dump_node(node.second),
        }
    if isinstance(node, Tabs):
        return {"type": "tabs", "panels": list(node.panels), "active": node.active}
    if isinstance(node, Stack):
        return {
            "type": "stack",
            "entries": [
                {"panel": entry.panel, "open": entry.open, "height": entry.height}
                for entry in node.entries
            ],
        }
    assert_never(node)


def _dump_floating(groups: Iterable[Floating[str]]) -> list[dict[str, object]]:
    return [
        {"panels": list(group.panels), "active": group.active, "rect": list(group.rect)}
        for group in groups
    ]


def _load_node(value: object) -> Node[str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LayoutFormatError("dock node must be an object")
    kind = value.get("type")
    if kind == "split":
        axis = _enum(SplitAxis, value.get("axis"), "axis")
        size = _load_size(value.get("size"))
        minimum = value.get("minimum", 0.0)
        if isinstance(minimum, bool) or not isinstance(minimum, int | float):
            raise LayoutFormatError("split minimum must be a number")
        return Split(
            axis,
            size,
            _need(_load_node(value.get("first"))),
            _need(_load_node(value.get("second"))),
            float(minimum),
        )
    if kind == "tabs":
        return Tabs(_strings(value.get("panels"), "panels"), _index(value.get("active")))
    if kind == "stack":
        entries = value.get("entries")
        if not isinstance(entries, list):
            raise LayoutFormatError("stack entries must be a list")
        return Stack([_load_entry(item) for item in entries])
    raise LayoutFormatError("dock node type is unknown")


def _need(node: Node[str] | None) -> Node[str]:
    if node is None:
        raise LayoutFormatError("split child is missing")
    return node


def _load_size(value: object) -> SplitSize:
    if not isinstance(value, dict):
        raise LayoutFormatError("split size must be an object")
    mode = _enum(SizeMode, value.get("mode"), "size mode")
    number = value.get("value")
    if isinstance(number, bool) or not isinstance(number, int | float):
        raise LayoutFormatError("split size value must be a number")
    return SplitSize(mode, float(number))


def _load_entry(value: object) -> StackEntry[str]:
    if not isinstance(value, dict) or not isinstance(value.get("panel"), str):
        raise LayoutFormatError("stack entry needs a string panel")
    height = value.get("height")
    if height is not None and (isinstance(height, bool) or not isinstance(height, int | float)):
        raise LayoutFormatError("stack height must be a number or null")
    return StackEntry(
        value["panel"], bool(value.get("open")), None if height is None else float(height)
    )


def _load_floating(value: object) -> Floating[str]:
    if not isinstance(value, dict):
        raise LayoutFormatError("floating group must be an object")
    rect = value.get("rect")
    if (
        not isinstance(rect, list)
        or len(rect) != 4
        or any(isinstance(item, bool) or not isinstance(item, int | float) for item in rect)
    ):
        raise LayoutFormatError("floating rect must be four numbers")
    return Floating(
        _strings(value.get("panels"), "panels"),
        _index(value.get("active")),
        (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
    )


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LayoutFormatError(f"{label} must be a list of strings")
    return list(value)


def _index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LayoutFormatError("active index must be a non-negative integer")
    return value


def _enum(kind: type[StrEnum], value: object, label: str) -> StrEnum:
    if not isinstance(value, str):
        raise LayoutFormatError(f"{label} must be text")
    try:
        return kind(value)
    except ValueError as exc:
        raise LayoutFormatError(f"unknown {label}") from exc
