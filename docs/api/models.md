# models

Shared base-model registry and quant selector for generation and LoRA training. `z-image-turbo` (Z-Image-Turbo, Apache-2.0) is the default from `default_model`. `z-image` (full Z-Image) is an optional download and the LoRA training base. `moss` (MOSS-Image, Apache-2.0) is an optional download. The setting value `z-image` is not an alias of Turbo. Quants are `bf16`, `fp8`, `int8`, and `nf4`, best quality first. `select_quant` picks the first whose estimated peak fits the budget (default `MEMORY_BUDGET_GB`, 14 GB) for `generate` (a LoRA loaded) or `train`. The peaks in `MODEL_PEAK_GB` are placeholders pending VM calibration. `normalize_base` maps an empty or missing LoRA tag, and `z-image-turbo`, to `z-image` (the Z-Image family) and passes `moss` and `z-image` through. `lora_base_matches` compares family ids, so a Z-Image LoRA matches both `z-image` and `z-image-turbo`.

The stored choice is the suite settings `model.default` (`z-image-turbo`) and `model.quant` (`auto`, or one quant). `auto` means calling `select_quant`. This module does not download anything and does not import torch.

::: voxprint_common.models
