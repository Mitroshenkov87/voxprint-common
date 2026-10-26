# ui.check_gate

Check button that blocks Next until a background check passes. Importing this module loads PySide6. The other UI modules listed on the [shared UI](../ui.md) page do not.

`GateState` is `DIRTY`, `RUNNING`, `PASSED`, `FAILED`, or `CANCELLED`. Dirty, failed, and cancelled paint a red stop. Passed paints a green arrow. Running paints a static accent arc. A handled failure uses `PASSED` or `FAILED`. There is no extra value.

`CheckItem` is `id`, `label`, and `active` (default true). `ItemResult` is `id`, `ok`, and `message`. `CancelToken.is_cancelled` is what the job reads after Cancel.

`CheckGate(job, *, items, unload=None, on_deactivate=None, parent=None)` starts dirty. `state`, `can_proceed`, `results`, and `deactivated` are the readouts. `can_proceed` is true only in `PASSED`. `mark_dirty()` turns the gate red and does not run the job. `start()` checks active items on a worker. `cancel()` stops after the current item. `unload` runs on that worker when the run ends. `on_deactivate` runs on the UI thread with the failed ids when the check finishes. If it returns and at least one checked item passed, the gate is `PASSED` and `deactivated` lists those ids.

Signals: `stateChanged`, `canProceedChanged`, `progress` (`done`, `total`, item id), and `finished`.
