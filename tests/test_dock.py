"""Dock transactions, geometry, and schema 1 round-trips."""

from __future__ import annotations

import math

import pytest

from voxprint_common.ui.dock import (
    MAX_DEPTH,
    MAX_PANELS,
    Activate,
    Close,
    DockError,
    DockErrorKind,
    DockHistory,
    DropTarget,
    Float,
    Floating,
    Layout,
    LayoutFormatError,
    Move,
    MoveFloating,
    Open,
    OpenAt,
    Permissions,
    Rect,
    ResizeSplit,
    ResizeStack,
    SetStackOpen,
    Split,
    SplitAxis,
    SplitPlacement,
    SplitSize,
    Stack,
    StackEntry,
    TabPlacement,
    Tabs,
    Zone,
    arrow_delta,
    drop_zone,
    dump_layout,
    fit_tabs,
    fit_toolbar,
    layout_from_json,
    layout_json,
    load_layout,
    resolve_drop,
    split_rect,
)


def _tabs(panels: list[int], active: int) -> Tabs[int]:
    return Tabs(panels, active)


def _sample() -> Layout[int]:
    return Layout(
        Split(
            SplitAxis.HORIZONTAL,
            SplitSize.fixed_second(240.0),
            _tabs([1], 0),
            _tabs([2, 3, 4], 1),
        ),
        [Floating([5, 6], 1, (80.0, 70.0, 300.0, 220.0))],
    )


def _move(panel: int, anchor: int, zone: Zone) -> Move[int]:
    return Move(panel, anchor, SplitPlacement(zone))


def test_floating_panel_joins_tabs_without_losing_others() -> None:
    layout = _sample()
    layout.apply(Move(5, 3, TabPlacement(4)))
    root = layout.root
    assert isinstance(root, Split)
    assert root.size == SplitSize.fixed_second(240.0)
    assert root.second == _tabs([2, 3, 5, 4], 2)
    assert layout.floating[0].panels == [6]
    assert layout.floating[0].active == 0
    layout.validate()
    assert len(layout.panels()) == 6


def test_detach_redock_merge_and_collapse() -> None:
    layout = _sample()
    layout.apply(Float(1, (30.0, 40.0, 250.0, 200.0)))
    assert layout.root == _tabs([2, 3, 4], 1)
    layout.apply(_move(1, 2, Zone.LEFT))
    assert len(layout.floating) == 1
    layout.apply(_move(5, 6, Zone.CENTER))
    assert layout.floating[0].panels == [6, 5]
    assert layout.floating[0].active == 1
    layout.apply(_move(6, 3, Zone.CENTER))
    layout.apply(_move(5, 3, Zone.CENTER))
    assert layout.floating == []
    assert len(layout.panels()) == 6


def test_invalid_moves_leave_the_layout_unchanged() -> None:
    layout = _sample()
    actions = [
        _move(5, 99, Zone.CENTER),
        _move(99, 2, Zone.CENTER),
        _move(5, 6, Zone.LEFT),
        _move(5, 5, Zone.CENTER),
        Move(5, 2, TabPlacement(6)),
        Float(3, (math.nan, 0.0, 100.0, 100.0)),
        Float(3, (0.0, 0.0, 0.0, 100.0)),
        ResizeSplit((False,), SplitSize.ratio(0.3)),
    ]
    for action in actions:
        changed = Layout(layout.root, list(layout.floating))
        # Deep equality needs a real copy; apply must not mutate on failure.
        before = layout_json(_as_text(layout))
        with pytest.raises(DockError):
            layout.apply(action)
        assert layout_json(_as_text(layout)) == before
        del changed


def test_closing_an_earlier_tab_keeps_the_selected_panel() -> None:
    layout = Layout(_tabs([1, 2, 3, 4], 2))
    layout.apply(Close(1))
    assert layout.root == _tabs([2, 3, 4], 1)
    layout.apply(Close(3))
    assert layout.root == _tabs([2, 4], 1)


def test_reorder_uses_the_destination_after_removal() -> None:
    layout = Layout(_tabs([1, 2, 3], 0))
    layout.apply(Move(1, 2, TabPlacement(None)))
    assert layout.root == _tabs([2, 3, 1], 2)
    layout.apply(Move(1, 2, TabPlacement(2)))
    assert layout.root == _tabs([1, 2, 3], 0)
    before = layout_json(_as_text(layout))
    layout.apply(Move(1, 2, TabPlacement(1)))
    assert layout_json(_as_text(layout)) == before


def test_accordion_state_survives_reorder_and_restore() -> None:
    layout: Layout[int] = Layout(
        Stack(
            [
                StackEntry(1, False, 123.0),
                StackEntry(2, True, None),
            ]
        )
    )
    layout.apply(Move(1, 2, TabPlacement(None)))
    root = layout.root
    assert isinstance(root, Stack)
    assert root.entries[1] == StackEntry(1, False, 123.0)
    saved = layout.location(1)
    layout.apply(Close(1))
    layout.restore(1, saved)
    root = layout.root
    assert isinstance(root, Stack)
    assert root.entries[1] == StackEntry(1, False, 123.0)


def test_protected_centre_rejects_merge_and_allows_a_side_split() -> None:
    def permissions(panel: int) -> Permissions:
        if panel == 1:
            return Permissions.protected()
        return Permissions()

    layout = _sample()
    refused = [
        Close(1),
        Float(1, (10.0, 10.0, 200.0, 200.0)),
        _move(1, 3, Zone.CENTER),
        _move(3, 1, Zone.CENTER),
        OpenAt(1, 3, TabPlacement(None)),
    ]
    for action in refused:
        before = layout_json(_as_text(layout))
        with pytest.raises(DockError) as caught:
            layout.apply(action, permissions)
        assert caught.value.kind is DockErrorKind.NOT_ALLOWED
        assert layout_json(_as_text(layout)) == before
    layout.apply(OpenAt(7, 1, SplitPlacement(Zone.LEFT)), permissions)
    assert layout.contains(1) and layout.contains(7)


def test_close_and_restore_keep_fixed_and_floating_geometry() -> None:
    layout: Layout[int] = Layout(
        Split(SplitAxis.HORIZONTAL, SplitSize.fixed_first(180.0), _tabs([1], 0), _tabs([2], 0)),
        [Floating([3], 0, (41.0, 52.0, 233.0, 244.0))],
    )
    saved = layout_json(_as_text(layout))
    first = layout.location(1)
    floating = layout.location(3)
    layout.apply(Close(1))
    layout.apply(Close(3))
    layout.restore(1, first)
    layout.restore(3, floating)
    assert layout_json(_as_text(layout)) == saved
    layout.apply(Close(1))
    before = layout_json(_as_text(layout))
    invalid = layout.location(2)
    invalid.anchor = 99
    with pytest.raises(DockError) as caught:
        layout.restore(1, invalid)
    assert caught.value.kind is DockErrorKind.MISSING_TARGET
    assert layout_json(_as_text(layout)) == before


def test_restoring_an_accordion_keeps_its_kind_and_heights() -> None:
    layout: Layout[int] = Layout(
        Split(
            SplitAxis.HORIZONTAL,
            SplitSize.fixed_second(220.0),
            _tabs([1], 0),
            Stack(
                [
                    StackEntry(2, False, 90.0),
                    StackEntry(3, True, 180.0),
                ]
            ),
        )
    )
    before = layout_json(_as_text(layout))
    second = layout.location(2)
    layout.apply(Close(2))
    third = layout.location(3)
    layout.apply(Close(3))
    layout.restore(3, third)
    layout.restore(2, second)
    assert layout_json(_as_text(layout)) == before


def test_invalid_layouts_are_rejected() -> None:
    duplicate = _sample()
    duplicate.floating[0].panels.append(1)
    with pytest.raises(DockError) as caught:
        duplicate.validate()
    assert caught.value.kind is DockErrorKind.DUPLICATE_PANEL
    invalid = _sample()
    invalid.floating[0].active = 2**30
    with pytest.raises(DockError) as caught:
        invalid.validate()
    assert caught.value.kind is DockErrorKind.INVALID_SELECTION
    invalid.floating[0].active = 0
    invalid.floating[0].rect = (0.0, 0.0, 10.0, math.inf)
    with pytest.raises(DockError) as caught:
        invalid.validate()
    assert caught.value.kind is DockErrorKind.INVALID_GEOMETRY
    deep: Tabs[int] | Split[int] = _tabs([0], 0)
    for panel in range(1, MAX_DEPTH + 1):
        deep = Split(SplitAxis.HORIZONTAL, SplitSize.ratio(0.5), deep, _tabs([panel], 0))
    too_deep = Layout(deep)
    with pytest.raises(DockError) as caught:
        too_deep.validate()
    assert caught.value.kind is DockErrorKind.LIMIT_EXCEEDED
    with pytest.raises(DockError) as caught:
        too_deep.apply(Activate(0))
    assert caught.value.kind is DockErrorKind.LIMIT_EXCEEDED
    oversized = Layout(Tabs(list(range(MAX_PANELS + 1)), 0))
    with pytest.raises(DockError) as caught:
        oversized.validate()
    assert caught.value.kind is DockErrorKind.LIMIT_EXCEEDED


def test_every_transaction_and_history() -> None:
    layout: Layout[str] = Layout(
        Stack([StackEntry("canvas", True, None), StackEntry("prompt", True, 40.0)])
    )
    history = DockHistory(layout)
    history.apply(SetStackOpen("prompt", False))
    history.apply(ResizeStack("prompt", 80.0))
    root = history.layout.root
    assert isinstance(root, Stack)
    assert root.entries[1] == StackEntry("prompt", False, 80.0)
    history.apply(Open("layers", "prompt"))
    history.apply(Move("layers", "canvas", SplitPlacement(Zone.RIGHT)))
    history.apply(ResizeSplit((), SplitSize.ratio(0.25)))
    root = history.layout.root
    assert isinstance(root, Split)
    assert root.size == SplitSize.ratio(0.25)
    assert root.minimum == 0.0
    history.apply(Float("prompt", (12.0, 16.0, 200.0, 180.0)))
    history.apply(MoveFloating("prompt", (20.0, 24.0, 220.0, 160.0)))
    assert history.layout.floating[0].rect == (20.0, 24.0, 220.0, 160.0)
    history.apply(Activate("canvas"))
    history.apply(Close("layers"))
    assert history.can_undo
    history.undo()
    assert history.layout.contains("layers")
    history.redo()
    assert not history.layout.contains("layers")
    with pytest.raises(DockError) as caught:
        DockHistory(Layout()).undo()
    assert caught.value.kind is DockErrorKind.EMPTY_HISTORY
    encoded = layout_json(history.layout)
    assert layout_json(layout_from_json(encoded)) == encoded
    with pytest.raises(LayoutFormatError):
        load_layout({"schema": 2})
    with pytest.raises(LayoutFormatError):
        load_layout({"schema": 1, "root": {"type": "nope"}, "floating": []})


def test_eight_hundred_moves_keep_every_panel() -> None:
    layout = _sample()
    seed = 72
    for step in range(800):
        seed = (seed * 6364136223846793005 + 1) & ((1 << 64) - 1)
        panel = seed % 6 + 1
        anchor = (seed >> 16) % 6 + 1
        if step % 7 == 0:
            action: Move[int] | Float[int] = Float(panel, (10.0, 20.0, 200.0, 220.0))
        else:
            zone = (Zone.LEFT, Zone.RIGHT, Zone.TOP, Zone.BOTTOM, Zone.CENTER)[seed % 5]
            action = _move(panel, anchor, zone)
        before = layout_json(_as_text(layout))
        try:
            layout.apply(action)
        except DockError:
            assert layout_json(_as_text(layout)) == before
        layout.validate()
        assert sorted(layout.panels()) == [1, 2, 3, 4, 5, 6]


def test_pointer_targets() -> None:
    rect = Rect(10.0, 20.0, 300.0, 200.0)
    strip = Rect(rect.x, rect.y, rect.width, 24.0)
    tabs = tuple(
        (panel, Rect(rect.x + index * 60.0, rect.y, 60.0, 24.0))
        for index, panel in enumerate((2, 3))
    )
    group = DropTarget(2, rect, strip, tabs, True, True)
    placement = resolve_drop(5, 90.0, 30.0, (group,))
    assert placement is not None
    assert placement.before == 3
    assert placement.insertion is not None
    centre = resolve_drop(5, *rect.center(), (group,))
    assert centre is not None and centre.zone is Zone.CENTER
    left = resolve_drop(5, 11.0, 90.0, (group,))
    assert left is not None and left.zone is Zone.LEFT
    assert resolve_drop(3, *rect.center(), (group,)) is None
    assert resolve_drop(3, 11.0, 90.0, (group,)) is not None
    overlay = DropTarget(5, rect, strip, ((5, strip),), False, False)
    assert resolve_drop(3, *rect.center(), (group, overlay)) is None
    assert drop_zone(Rect(0.0, 0.0, 0.0, 0.0), 24.0, 0.0, 0.0) is None
    assert drop_zone(rect, 24.0, math.nan, 0.0) is None


def test_split_minimum_and_arrow_step() -> None:
    area = Rect(0.0, 0.0, 200.0, 100.0)
    laid = split_rect(area, SplitAxis.HORIZONTAL, SplitSize.ratio(0.5), 4.0, 40.0)
    assert laid is not None
    assert laid.first.width == pytest.approx(98.0)
    moved = laid.resized(SplitSize.ratio(0.5), arrow_delta(SplitAxis.HORIZONTAL, "Right"), 40.0)
    assert moved is not None
    assert moved.mode.value == "ratio"
    assert moved.value == pytest.approx((98.0 + 8.0) / laid.available)
    assert laid.resized(SplitSize.ratio(0.5), math.nan, 40.0) is None
    assert arrow_delta(SplitAxis.VERTICAL, "Up") == -8.0
    assert arrow_delta(SplitAxis.VERTICAL, "Left") == 0.0
    assert split_rect(area, SplitAxis.HORIZONTAL, SplitSize.ratio(math.nan), 4.0, 0.0) is None


def test_tab_fit_keeps_the_selected_tab() -> None:
    fitted = fit_tabs((80.0, 80.0, 80.0), 2, 100.0, 40.0, 20.0)
    assert 2 in {index for index, _width in fitted.shown}
    assert fitted.overflow
    assert sum(width for _index, width in fitted.shown) + fitted.overflow_width <= 100.0
    exact = fit_tabs((20.0, 30.0), 0, 50.0, 10.0, 16.0)
    assert exact.overflow == ()
    assert exact.overflow_width == 0.0


def test_toolbar_reserves_a_real_overflow_control() -> None:
    exact = fit_toolbar((24.0, 36.0, 48.0), 120.0, 6.0, 20.0)
    assert exact is not None and exact.overflow == ()
    packed = fit_toolbar((24.0, 36.0, 48.0), 96.0, 4.0, 20.0)
    assert packed == type(exact)(((0, 24.0), (1, 36.0)), (2,), 20.0)
    assert fit_toolbar((200.0, 24.0), 120.0, 4.0, 20.0) == type(exact)((), (0, 1), 20.0)
    assert fit_toolbar((30.0, 30.0), 10.0, 4.0, 20.0) is None
    assert fit_toolbar((24.0,), math.nan, 4.0, 20.0) is None
    assert fit_toolbar((), 0.0, 6.0, 20.0) is not None


def _as_text(layout: Layout[int]) -> Layout[str]:
    payload = dump_layout(_renumber(layout))
    return load_layout(payload)


def _renumber(layout: Layout[int]) -> Layout[str]:
    text = layout_json(_map_layout(layout))
    return layout_from_json(text)


def _map_layout(layout: Layout[int]) -> Layout[str]:
    return Layout(
        None if layout.root is None else _map_node(layout.root),
        [
            Floating([str(panel) for panel in group.panels], group.active, group.rect)
            for group in layout.floating
        ],
    )


def _map_node(node: Split[int] | Tabs[int] | Stack[int]) -> Split[str] | Tabs[str] | Stack[str]:
    if isinstance(node, Tabs):
        return Tabs([str(panel) for panel in node.panels], node.active)
    if isinstance(node, Stack):
        return Stack(
            [StackEntry(str(entry.panel), entry.open, entry.height) for entry in node.entries]
        )
    return Split(node.axis, node.size, _map_node(node.first), _map_node(node.second), node.minimum)
