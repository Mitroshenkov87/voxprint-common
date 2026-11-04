# models

Shared base-model registry and quant selector for generation and LoRA training. `moss` (MOSS-Image, Apache-2.0) is the default from `default_model`. `z-image` is an optional separate download. Quants are `bf16`, `fp8`, `int8`, and `nf4`, best quality first. `select_quant` picks the first whose estimated peak fits the budget (default `MEMORY_BUDGET_GB`, 14 GB) for `generate` (a LoRA loaded) or `train`. The peaks in `MODEL_PEAK_GB` are placeholders pending VM calibration. `normalize_base` maps an empty or missing LoRA tag to `z-image` and passes `moss` and `z-image` through. `lora_base_matches` is that comparison.

The stored choice is the suite settings `model.default` (`moss`) and `model.quant` (`auto`, or one quant). `auto` means calling `select_quant`. This module does not download anything and does not import torch.

::: voxprint_common.models
