# image

Shared image save format and metadata. The format is the `image.save_format` suite setting: lossless WebP by default, lossy WebP at a quality of 1-100, or PNG, AVIF, or JPEG. `default_quality` is lossless for masters, series, and hand-offs. `save_image(..., purpose="picture")` with no explicit quality saves lossy WebP at quality 90 (`LOSSY_DEFAULT_QUALITY`). `save_image` writes atomically and fixes the extension.

Metadata (`prompt`, `seed`, `steps`, `base` with `base_model` accepted on input and read back as both, `lora_strength`, and `loras`) is off unless `write_metadata=True` or the setting's `metadata` key is true. Passed metadata is ignored while that switch is off. It is written as XMP on WebP, AVIF, and JPEG, plus a minimal EXIF ImageDescription and UserComment JSON copy, and as a PNG iTXt chunk keyed `XML:com.adobe.xmp`. `read_metadata` reads that XMP from all four formats. Each LoRA comes back as `{name, strength, sha256, base}`; a missing LoRA base is None. Multi-line prompts round-trip, including LF and CRLF. Pillow is an optional extra and is never imported at load.

::: voxprint_common.image
