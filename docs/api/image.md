# image

Shared image save format and WebP XMP metadata. The format is the `image.save_format` suite setting: lossless WebP by default, lossy WebP with a quality, or PNG, AVIF, or JPEG. `save_image` writes atomically and fixes the extension. Pillow is an optional extra and is never imported at load.

::: voxprint_common.image
