"""Which dock panels are expert, and the view that hides them.

The stored layout keeps every panel. :func:`visible_layout` returns a copy
with expert panels removed while Expert mode is off. Turning the mode on and
calling it again shows the same tree. Qt is not imported.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass

from voxprint_common.ui.dock.model import Close, Layout

__all__ = ["PanelSpec", "visible_layout"]


@dataclass(frozen=True)
class PanelSpec:
    """One dock panel. ``expert`` panels stay in the layout JSON while hidden."""

    panel_id: str
    title_key: str
    expert: bool = False


def visible_layout(
    layout: Layout[str],
    panels: Iterable[PanelSpec],
    *,
    expert: bool,
) -> Layout[str]:
    """Return a copy of ``layout`` for the current mode.

    When ``expert`` is false, panels tagged expert are closed on the copy.
    Empty groups collapse. ``layout`` itself is not changed, so
    :func:`voxprint_common.ui.dock.layout_json` still contains the hidden
    panels and a later call with ``expert`` true brings them back.

    Args:
        layout: The stored tree. Panel ids are strings.
        panels: Catalog that says which ids are expert. Untagged ids stay.
        expert: True when Expert mode is on.

    Returns:
        A new layout. The input is left as it was.
    """
    shown = copy.deepcopy(layout)
    if expert:
        return shown
    hidden = {spec.panel_id for spec in panels if spec.expert}
    for panel_id in tuple(shown.panels()):
        if panel_id in hidden and shown.contains(panel_id):
            shown.apply(Close(panel_id))
    return shown
