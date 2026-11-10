# MDL model display fields

Model entries can carry three optional display strings — `label`, `purpose`, and `group` — that drive the grouped Manage models window. They exist on both manifest forms: schema 1 `mdl.json` model objects and `voxprint-app-models/1` tier entries (object form) plus `optional[]` entries. Every field is optional; files written before 0.7.1 load and render unchanged.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `label` | string | Short display name. Fallback: `display_name`, then the model `id`. |
| `purpose` | string | One line describing what the model does for the user. Empty when absent. |
| `group` | string | Task group id, one of the values below. Missing, empty, or unknown values sort into `other`; the load never fails on a bad group. |

`label` and `purpose` must be strings when present (empty string is allowed); anything else is a `CatalogError` / `DescriptorError`. The same rule applies to `group`. `vram_tier` keeps its existing meaning (`any`, `8gb`, `16gb`, `24gb`) and is unchanged by this spec.

Schema 1 example:

```json
{"id": "z-image-turbo-bf16-16gb", "vram_tier": "16gb", "group": "generate",
 "label": "Z-Image Turbo for 16 GB cards",
 "purpose": "The default model, sized to fit 16 GB graphics cards."}
```

`voxprint-app-models/1` example, on a tier entry and on an optional entry:

```json
{"schema": "voxprint-app-models/1", "app_id": "canvas",
 "tiers": {"16gb": [{"id": "z-image-turbo-bf16-16gb", "group": "generate",
                     "label": "Z-Image Turbo for 16 GB cards",
                     "purpose": "The default model, sized to fit 16 GB graphics cards."}]},
 "runtimes": [],
 "optional": [{"id": "qwen3-4b-writer", "min_vram_gib": null, "group": "writer",
               "label": "Photoshoot writer (Qwen3 4B)",
               "purpose": "Writes the shot list for Photoshoot series."}]}
```

When the same id appears in more than one tier entry, the first non-empty `label`, `purpose`, and `group` wins, tier entries before `optional[]`.

## Task groups and display order

`group` takes one of: `generate`, `edit`, `upscale`, `restore`, `voice`, `transcribe`, `translate`, `writer`, `safety`, `internal`. The manage page shows groups in this order, with `other` (the fallback bucket) just before `safety`, and hides groups that have no rows:

1. generate — "Generate"
2. edit — "Edit & structure"
3. upscale — "Upscale"
4. restore — "Restore"
5. voice — "Voice & speech"
6. transcribe — "Speech to text"
7. translate — "Translate"
8. writer — "Writer"
9. other — "Other" (missing or unknown group)
10. safety — "Required for safety"
11. internal — "Built-in helpers"

Installed and available models appear together in their task group; each row keeps its state chip (Installed / Needs repair / Not installed) and its action (Download / Remove / Repair). The row title is `label`, falling back to `display_name`, then the raw `id`, which also sits in the row tooltip.

## VRAM tier split and badges

`vram_tier` interacts with the card the page was opened for. Download planning is untouched: `job.detect_tier` / `plan_files` still treat an unknown GPU as 8 GB. Only the manage-page layout differs: when no GPU is detected (`VramTier.UNKNOWN` or an empty device list) the page assumes 16 GB for which subsection is open.

The split applies only inside `generate`, `edit`, `upscale`, and `restore`, and only when that group holds both a compact tier (`8gb` or `16gb`) and a full-size (`24gb`) row:

- `16gb` (and `8gb`, when present) rows go in "For 16 GB cards (compact)".
- `24gb` rows go in "Full size (24 GB+)".
- Every row is badged with its tier (`8gb`, `16gb`, `24gb`); `any` gets no badge.
- The subsection matching the detected card starts open: "Full size (24 GB+)" on a 24 GB card, the compact section on a 16 GB or 8 GB card, or when no GPU was detected. The other subsection starts collapsed under "Show models for other cards".
- `any`-tier rows in a split group join the open subsection, with no badge.

A group without both halves, and every group outside the four split candidates, paints a flat list; rows still carry tier badges. `Controller.listing()` keeps hiding models above the card's tier (downloads and readiness depend on it); `Controller.manage_page()` alone lists other-tier models so they remain addable from the collapsed section — nothing is auto-downloaded.

## Protected groups

`safety` and `internal` are required for the checks the suite runs around generation. Both start collapsed, paint no Remove button, and are refused in logic: `voxprint_common.mdl.present.can_remove(group)` returns False for them, `Controller.remove_model(app, component, group=...)` returns False without touching the store, and the window action refuses Remove even when a button is forced. Repair and Download still appear when relevant. Callers that do not pass `group` keep the previous behaviour. `present.PROTECTED_GROUPS` is the frozen set of the two ids.

## Backward compatibility

Old `mdl.json` and `voxprint-app-models.json` files without the new fields load, plan, download, and render exactly as before. `catalog.Model` gains `label`, `purpose`, and `group` defaulting to `""`, so every existing `Model(...)` construction stays valid, and `ManageRow` / `ManageView` gain their new fields with defaults for the same reason. A manifest that sets `group` to a value outside the list above still loads; the row sorts into "Other".
