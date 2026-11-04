"""Shared image saving and WebP metadata.

The save format is the shared ``image.save_format`` suite setting: lossless
WebP by default, lossy WebP with a quality of 1-100, or PNG, AVIF, or JPEG.
:func:`save_image` encodes first and publishes the bytes atomically, and the
written file carries the extension of the chosen format. ``prompt``, ``seed``,
and the LoRA list are written as XMP in WebP and read back with
:func:`read_metadata`.

Pillow is an optional extra. Install it with ``voxprint-common[image]``.
Importing this module does not import Pillow.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from voxprint_common.atomic import write_bytes
from voxprint_common.suite_settings import image_save_format, normalize_image_save_format

if TYPE_CHECKING:
    from PIL import Image

__all__ = [
    "build_xmp",
    "parse_xmp",
    "read_metadata",
    "save_image",
]

_EXTENSIONS = {"webp": ".webp", "png": ".png", "avif": ".avif", "jpeg": ".jpg"}
_PIL_FORMATS = {"webp": "WEBP", "png": "PNG", "avif": "AVIF", "jpeg": "JPEG"}
# Formats a Pillow build may lack. The value is the ``PIL.features`` name.
_PIL_FEATURES = {"avif": "avif", "jpeg": "jpg"}
_XMP_NS = "https://voxprint.ai/ns/image/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_QUOTE_ESCAPES = {'"': "&quot;"}


def save_image(
    image: Image.Image,
    path: Path | str,
    setting: Mapping[str, Any] | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Save ``image`` with the shared save format, or an explicit one.

    Encoding happens in memory first; the bytes are then published with
    :func:`voxprint_common.atomic.write_bytes`, so a reader sees the previous
    file or the finished one. ``path`` gets the extension of the chosen
    format. JPEG is ``.jpg``.

    Args:
        image: A Pillow image.
        path: Destination. A suffix that does not match the format is replaced.
        setting: ``{"format": ..., "quality": ...}``, a format name such as
            ``"webp"``, or None for the shared ``image.save_format`` setting.
        metadata: ``prompt``, ``seed``, and ``loras`` written as XMP. Only
            WebP carries XMP; with another format this raises.

    Returns:
        The path that was written.

    Raises:
        ImportError: Pillow is not installed.
        ValueError: ``setting`` is not a valid save format, or ``metadata`` is
            given for a format that cannot carry it.
        RuntimeError: The format needs a Pillow feature this build lacks.
    """
    chosen = _resolve_setting(setting)
    fmt = str(chosen["format"])
    quality = chosen["quality"]
    if metadata and fmt != "webp":
        raise ValueError(f"{fmt} cannot carry XMP metadata; use webp")
    _require_feature(fmt)
    if fmt == "jpeg" and image.mode in ("P", "RGBA", "LA"):
        image = image.convert("RGB")
    target = Path(path).with_suffix(_EXTENSIONS[fmt])
    kwargs: dict[str, Any] = {}
    if fmt == "webp":
        kwargs["lossless"] = quality is None
        if quality is not None:
            kwargs["quality"] = quality
        if metadata:
            kwargs["xmp"] = build_xmp(metadata)
    elif fmt != "png":
        kwargs["quality"] = quality
    buffer = io.BytesIO()
    image.save(buffer, format=_PIL_FORMATS[fmt], **kwargs)
    write_bytes(target, buffer.getvalue())
    return target


def read_metadata(path: Path | str) -> dict[str, Any]:
    """Read the XMP metadata of a WebP file.

    Args:
        path: A WebP file saved by :func:`save_image` or by another tool that
            writes Voxprint XMP.

    Returns:
        ``prompt``, ``seed``, and ``loras``. Missing entries are None and an
        empty list.

    Raises:
        ImportError: Pillow is not installed.
    """
    packet = _open_xmp(path)
    if packet is None:
        return {"prompt": None, "seed": None, "loras": []}
    return parse_xmp(packet)


def build_xmp(metadata: Mapping[str, Any]) -> bytes:
    """Build a Voxprint XMP packet.

    Args:
        metadata: May set ``prompt`` (text), ``seed`` (integer), and ``loras``
            (an iterable of LoRA weight names).

    Returns:
        The packet as UTF-8 bytes, ready for Pillow's ``xmp`` save argument.
    """
    prompt = metadata.get("prompt")
    seed = metadata.get("seed")
    loras = metadata.get("loras")
    attributes = ""
    if prompt is not None:
        attributes += f' vp:prompt="{escape(str(prompt), _QUOTE_ESCAPES)}"'
    if seed is not None:
        attributes += f' vp:seed="{int(seed)}"'
    items = ""
    if loras:
        names = "".join(f"<rdf:li>{escape(str(name))}</rdf:li>" for name in loras)
        items = f"<vp:loras><rdf:Bag>{names}</rdf:Bag></vp:loras>"
    packet = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        f'  <rdf:RDF xmlns:rdf="{_RDF_NS}">\n'
        f'    <rdf:Description rdf:about="" xmlns:vp="{_XMP_NS}"{attributes}>'
        f"{items}</rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def parse_xmp(packet: bytes | str) -> dict[str, Any]:
    """Read ``prompt``, ``seed``, and ``loras`` from a Voxprint XMP packet.

    A packet without the Voxprint namespace yields empty values.

    Args:
        packet: XMP bytes, for example Pillow's ``image.info["xmp"]``.

    Returns:
        ``prompt``, ``seed``, and ``loras``.
    """
    text = packet.decode("utf-8", "replace") if isinstance(packet, bytes) else packet
    found: dict[str, Any] = {"prompt": None, "seed": None, "loras": []}
    root = ElementTree.fromstring(text)
    description = root.find(f".//{{{_RDF_NS}}}Description")
    if description is None:
        return found
    prompt = description.get(f"{{{_XMP_NS}}}prompt")
    if prompt is not None:
        found["prompt"] = prompt
    raw_seed = description.get(f"{{{_XMP_NS}}}seed")
    if raw_seed is not None:
        with contextlib.suppress(ValueError):
            found["seed"] = int(raw_seed)
    bag = description.find(f"{{{_XMP_NS}}}loras/{{{_RDF_NS}}}Bag")
    if bag is not None:
        found["loras"] = [item.text or "" for item in bag.findall(f"{{{_RDF_NS}}}li")]
    return found


def _resolve_setting(setting: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if setting is None:
        return image_save_format()
    stored = normalize_image_save_format(setting)
    if stored is None:
        raise ValueError(f"not an image save format: {setting!r}")
    return stored


def _require_feature(fmt: str) -> None:
    feature = _PIL_FEATURES.get(fmt)
    if feature is None:
        return
    features = _pillow_features()
    if not features.check(feature):
        raise RuntimeError(
            f"saving {fmt.upper()} needs a Pillow build with {feature} support; "
            "install a full Pillow wheel (voxprint-common[image])"
        )


def _open_xmp(path: Path | str) -> bytes | None:
    Image = _pillow()
    with Image.open(Path(path)) as image:
        packet = image.info.get("xmp")
    if isinstance(packet, str):
        return packet.encode("utf-8")
    return packet


def _pillow() -> Any:
    """Import Pillow's ``Image`` module.

    Pillow is an optional extra. The import stays inside this function so the
    module loads, and apps without images run, when Pillow is not installed.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError("image support needs Pillow; install voxprint-common[image]") from exc
    return Image


def _pillow_features() -> Any:
    """Import Pillow's ``features`` module. See :func:`_pillow`."""
    try:
        from PIL import features
    except ImportError as exc:
        raise ImportError("image support needs Pillow; install voxprint-common[image]") from exc
    return features
