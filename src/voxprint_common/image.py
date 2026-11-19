"""Shared image saving, metadata, and the WebP default.

The save format is the shared ``image.save_format`` suite setting: lossless
WebP by default, lossy WebP with a quality of 1-100, or PNG, AVIF, or JPEG.
A quality of None means unset: :func:`save_image` applies
:func:`default_quality` for the save purpose. :data:`QUALITY_LOSSLESS` (the
string ``"lossless"``) is explicit lossless, even for a regular picture.
:func:`default_quality` is :data:`QUALITY_LOSSLESS` for masters, series, and
hand-offs, and lossy WebP at :data:`LOSSY_DEFAULT_QUALITY` for regular
pictures. :func:`save_image` encodes first and publishes the bytes
atomically, and the written file carries the extension of the chosen format.

Metadata (``prompt``, ``seed``, ``steps``, ``base``, the LoRA list, and the
single-LoRA strength) is written as XMP on WebP, AVIF, and JPEG, and as an
iTXt chunk on PNG. A small EXIF copy (ImageDescription and UserComment holding
the same document as JSON) is added beside the XMP on WebP, AVIF, and JPEG.
Metadata is off by default; pass ``write_metadata=True`` or set the
``metadata`` key of ``image.save_format`` to opt in. :func:`read_metadata`
reads the XMP back from any of the four formats.

Pillow is an optional extra. Install it with ``voxprint-common[image]``.
Importing this module does not import Pillow.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from voxprint_common.atomic import write_bytes
from voxprint_common.suite_settings import image_save_format, normalize_image_save_format

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

__all__ = [
    "LOSSY_DEFAULT_QUALITY",
    "QUALITY_LOSSLESS",
    "build_xmp",
    "default_quality",
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
_PNG_XMP_KEY = "XML:com.adobe.xmp"
_TEXT_ESCAPES = {'"': "&quot;", "'": "&apos;"}
# Formats that carry the minimal EXIF copy beside the XMP.
_EXIF_FORMATS = frozenset({"webp", "avif", "jpeg"})
#: Lossy quality for regular pictures when nothing else says what to use.
LOSSY_DEFAULT_QUALITY = 90
#: Explicit-lossless quality marker. None means the quality is unset and the
#: save purpose default applies; ``"lossless"`` forces lossless WebP even for
#: a regular picture.
QUALITY_LOSSLESS = "lossless"
_PURPOSES = frozenset({None, "master", "picture"})

# Metadata keys read back with None/[] when missing.
_METADATA_KEYS = (
    "prompt",
    "seed",
    "steps",
    "base",
    "base_model",
    "lora_strength",
    "loras",
)


def default_quality(kind: str | None = None) -> int | str:
    """Return the WebP quality for a save purpose.

    Masters, series, and hand-offs (``"master"`` or None) are lossless, so
    the quality is :data:`QUALITY_LOSSLESS`. Regular pictures (``"picture"``)
    are lossy WebP at :data:`LOSSY_DEFAULT_QUALITY`.

    Args:
        kind: ``"master"``, ``"picture"``, or None.

    Returns:
        :data:`LOSSY_DEFAULT_QUALITY` for a picture, or
        :data:`QUALITY_LOSSLESS` for a lossless master.

    Raises:
        ValueError: ``kind`` is not ``"master"``, ``"picture"``, or None.
    """
    if kind not in _PURPOSES:
        raise ValueError(f"purpose must be 'master' or 'picture', got {kind!r}")
    if kind == "picture":
        return LOSSY_DEFAULT_QUALITY
    return QUALITY_LOSSLESS


def save_image(
    image: Image.Image,
    path: Path | str,
    setting: Mapping[str, Any] | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    write_metadata: bool | None = None,
    purpose: str | None = None,
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
            The quality is None (unset: the save purpose default applies),
            :data:`QUALITY_LOSSLESS` (explicit lossless WebP), or an integer
            from 1 to 100 (lossy WebP at that quality).
        metadata: ``prompt``, ``seed``, ``steps``, ``base`` (or its alias
            ``base_model``), ``lora_strength``, and ``loras``. LoRA entries
            are weight names or mappings with ``name``, ``strength``,
            ``sha256``, and ``base``. The mapping is written as XMP, plus a
            minimal EXIF copy on WebP, AVIF, and JPEG.
        write_metadata: True writes the metadata, False or leaving it None
            with the ``metadata`` setting off silently skips it. None uses
            the ``metadata`` key of the save format setting (default False).
        purpose: ``"picture"`` saves a regular picture: with the quality
            unset (None) the WebP default becomes lossy at
            :func:`default_quality` instead of lossless. An explicit
            :data:`QUALITY_LOSSLESS` quality stays lossless even here.
            ``"master"`` (the default) keeps the setting as stored: lossless
            WebP for masters, series, and hand-offs.

    Returns:
        The path that was written.

    Raises:
        ImportError: Pillow is not installed.
        ValueError: ``setting`` is not a valid save format, or ``purpose``
            is not ``"master"``, ``"picture"``, or None.
        RuntimeError: The format needs a Pillow feature this build lacks.
    """
    if purpose not in _PURPOSES:
        raise ValueError(f"purpose must be 'master' or 'picture', got {purpose!r}")
    chosen = _resolve_setting(setting)
    fmt = str(chosen["format"])
    quality = chosen["quality"]
    if quality is None and fmt == "webp":
        quality = default_quality(purpose)
    _require_feature(fmt)
    if fmt == "jpeg" and image.mode in ("P", "RGBA", "LA"):
        image = image.convert("RGB")
    write = bool(chosen["metadata"]) if write_metadata is None else bool(write_metadata)
    target = Path(path).with_suffix(_EXTENSIONS[fmt])
    kwargs: dict[str, Any] = {}
    if fmt == "webp":
        kwargs["lossless"] = quality == QUALITY_LOSSLESS or quality is None
        if quality is not None and quality != QUALITY_LOSSLESS:
            kwargs["quality"] = quality
    elif fmt != "png":
        kwargs["quality"] = quality
    if metadata and write:
        packet = build_xmp(metadata)
        if fmt == "png":
            kwargs["pnginfo"] = _png_info(packet)
        else:
            kwargs["xmp"] = packet
            if fmt in _EXIF_FORMATS:
                kwargs["exif"] = _build_exif(metadata)
    buffer = io.BytesIO()
    image.save(buffer, format=_PIL_FORMATS[fmt], **kwargs)
    write_bytes(target, buffer.getvalue())
    return target


def read_metadata(path: Path | str) -> dict[str, Any]:
    """Read the XMP metadata of a saved image.

    Works for WebP, AVIF, JPEG, and PNG files saved by :func:`save_image`,
    and for other tools that write Voxprint XMP (PNG: an iTXt chunk keyed
    ``XML:com.adobe.xmp``).

    Args:
        path: An image file.

    Returns:
        ``prompt``, ``seed``, ``steps``, ``base``, ``base_model``,
        ``lora_strength``, and ``loras``. Missing entries are None and an
        empty list. ``base`` and its alias ``base_model`` read back together,
        and LoRA entries are normalized to ``{"name", "strength", "sha256",
        "base"}`` dicts.

    Raises:
        ImportError: Pillow is not installed.
    """
    packet = _open_xmp(path)
    if packet is None:
        return {key: ([] if key == "loras" else None) for key in _METADATA_KEYS}
    return parse_xmp(packet)


def build_xmp(metadata: Mapping[str, Any]) -> bytes:
    """Build a Voxprint XMP packet.

    Args:
        metadata: May set ``prompt`` (text), ``seed`` (integer), ``steps``
            (integer), ``base`` or its alias ``base_model`` (a base model
            tag), ``lora_strength`` (float, the single-LoRA case), and
            ``loras`` (an iterable of LoRA weight names or mappings with
            ``name``, ``strength``, ``sha256``, and ``base``). A strength
            that is not a finite float (``inf`` or ``nan``) is omitted.

    Returns:
        The packet as UTF-8 bytes, ready for Pillow's ``xmp`` save argument
        or the PNG iTXt chunk.
    """
    document = _metadata_document(metadata)
    attributes = ""
    if document["seed"] is not None:
        attributes += f' vp:seed="{document["seed"]}"'
    if document["steps"] is not None:
        attributes += f' vp:steps="{document["steps"]}"'
    if document["base"] is not None:
        attributes += f' vp:base="{escape(str(document["base"]), _TEXT_ESCAPES)}"'
    if document["lora_strength"] is not None:
        attributes += f' vp:lora_strength="{document["lora_strength"]!r}"'
    elements = ""
    if document["prompt"] is not None:
        # XML parsers normalize literal CR and CRLF to LF on parse; character
        # references survive, so keep \r (and thus CRLF) out of the raw text.
        text = (
            escape(str(document["prompt"]), _TEXT_ESCAPES)
            .replace("\r\n", "&#13;\n")
            .replace("\r", "&#13;")
        )
        elements += f"<vp:prompt>{text}</vp:prompt>"
    if document["loras"]:
        items = "".join(_lora_xmp(lora) for lora in document["loras"])
        elements += f"<vp:loras><rdf:Bag>{items}</rdf:Bag></vp:loras>"
    packet = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        f'  <rdf:RDF xmlns:rdf="{_RDF_NS}">\n'
        f'    <rdf:Description rdf:about="" xmlns:vp="{_XMP_NS}"{attributes}>'
        f"{elements}</rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def parse_xmp(packet: bytes | str) -> dict[str, Any]:
    """Read the Voxprint metadata keys from an XMP packet.

    A packet without the Voxprint namespace yields empty values. A malformed
    packet logs a warning and yields the same empty values instead of raising.

    Args:
        packet: XMP bytes, for example Pillow's ``image.info["xmp"]`` or the
            PNG iTXt ``XML:com.adobe.xmp`` chunk.

    Returns:
        ``prompt``, ``seed``, ``steps``, ``base``, ``base_model``,
        ``lora_strength``, and ``loras`` (normalized to dicts).
    """
    text = packet.decode("utf-8", "replace") if isinstance(packet, bytes) else packet
    found: dict[str, Any] = {key: ([] if key == "loras" else None) for key in _METADATA_KEYS}
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, SyntaxError) as exc:
        logger.warning("discarding malformed XMP packet: %s", exc)
        return found
    description = root.find(f".//{{{_RDF_NS}}}Description")
    if description is None:
        return found
    prompt_element = description.find(f"{{{_XMP_NS}}}prompt")
    if prompt_element is not None:
        found["prompt"] = prompt_element.text or ""
    raw_seed = description.get(f"{{{_XMP_NS}}}seed")
    if raw_seed is not None:
        with contextlib.suppress(ValueError):
            found["seed"] = int(raw_seed)
    raw_steps = description.get(f"{{{_XMP_NS}}}steps")
    if raw_steps is not None:
        with contextlib.suppress(ValueError):
            found["steps"] = int(raw_steps)
    raw_base = description.get(f"{{{_XMP_NS}}}base")
    if raw_base is not None:
        found["base"] = raw_base
        found["base_model"] = raw_base
    raw_strength = description.get(f"{{{_XMP_NS}}}lora_strength")
    if raw_strength is not None:
        with contextlib.suppress(ValueError):
            found["lora_strength"] = float(raw_strength)
    bag = description.find(f"{{{_XMP_NS}}}loras/{{{_RDF_NS}}}Bag")
    if bag is not None:
        found["loras"] = [_parse_lora(item) for item in bag.findall(f"{{{_RDF_NS}}}li")]
    return found


def _metadata_document(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the metadata mapping for the XMP packet and the EXIF copy."""
    document: dict[str, Any] = {
        "prompt": None,
        "seed": None,
        "steps": None,
        "base": None,
        "lora_strength": None,
        "loras": [],
    }
    prompt = metadata.get("prompt")
    if prompt is not None:
        document["prompt"] = str(prompt)
    seed = metadata.get("seed")
    if seed is not None:
        document["seed"] = int(seed)
    steps = metadata.get("steps")
    if steps is not None:
        document["steps"] = int(steps)
    base = metadata.get("base", metadata.get("base_model"))
    if base is not None:
        document["base"] = str(base)
    strength = metadata.get("lora_strength")
    if strength is not None:
        value = float(strength)
        if math.isfinite(value):
            document["lora_strength"] = value
    loras = metadata.get("loras")
    if loras:
        document["loras"] = [_normalize_lora(lora) for lora in loras]
    return document


def _normalize_lora(entry: Any) -> dict[str, Any]:
    """Normalize one LoRA entry to ``{"name", "strength", "sha256", "base"}``."""
    if isinstance(entry, Mapping):
        name = entry.get("name")
        strength = entry.get("strength")
        sha256 = entry.get("sha256")
        base = entry.get("base", entry.get("base_model"))
    else:
        name, strength, sha256, base = entry, None, None, None
    return {
        "name": None if name is None else str(name),
        "strength": _as_float(strength),
        "sha256": None if sha256 is None else str(sha256),
        "base": None if base is None else str(base),
    }


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    with contextlib.suppress(TypeError, ValueError):
        result = float(value)
        if math.isfinite(result):
            return result
    return None


def _lora_xmp(lora: Mapping[str, Any]) -> str:
    name = lora["name"]
    strength = lora["strength"]
    sha256 = lora["sha256"]
    base = lora["base"]
    if strength is None and sha256 is None and base is None:
        return f"<rdf:li>{escape(str(name), _TEXT_ESCAPES)}</rdf:li>"
    attributes = f' vp:name="{escape(str(name), _TEXT_ESCAPES)}"'
    if strength is not None:
        attributes += f' vp:strength="{strength!r}"'
    if sha256 is not None:
        attributes += f' vp:sha256="{escape(str(sha256), _TEXT_ESCAPES)}"'
    if base is not None:
        attributes += f' vp:base="{escape(str(base), _TEXT_ESCAPES)}"'
    return f"<rdf:li><rdf:Description{attributes}/></rdf:li>"


def _parse_lora(item: ElementTree.Element) -> dict[str, Any]:
    nested = item.find(f"{{{_RDF_NS}}}Description")
    if nested is None:
        entry: dict[str, Any] = {"name": item.text or ""}
    else:
        entry = {
            "name": nested.get(f"{{{_XMP_NS}}}name"),
            "sha256": nested.get(f"{{{_XMP_NS}}}sha256"),
            "base": nested.get(f"{{{_XMP_NS}}}base"),
        }
        raw_strength = nested.get(f"{{{_XMP_NS}}}strength")
        entry["strength"] = _as_float(raw_strength) if raw_strength is not None else None
    return {
        "name": entry.get("name"),
        "strength": entry.get("strength"),
        "sha256": entry.get("sha256"),
        "base": entry.get("base"),
    }


def _exif_document(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Minimal JSON copy of the metadata for the EXIF fallback."""

    def drop_none(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: drop_none(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [drop_none(item) for item in value]
        return value

    return drop_none(_metadata_document(metadata))


def _build_exif(metadata: Mapping[str, Any]) -> Any:
    """Build a minimal EXIF block with the metadata JSON in two tags."""
    Image = _pillow()
    payload = json.dumps(_exif_document(metadata), ensure_ascii=True)
    exif = Image.Exif()
    exif[0x010E] = payload  # ImageDescription
    exif[0x9286] = b"ASCII\x00\x00\x00" + payload.encode("ascii")  # UserComment
    return exif


def _png_info(packet: bytes) -> Any:
    """Wrap the XMP packet in a PNG iTXt chunk under the Adobe XMP key."""
    try:
        from PIL import PngImagePlugin
    except ImportError as exc:
        raise ImportError("image support needs Pillow; install voxprint-common[image]") from exc
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_itxt(_PNG_XMP_KEY, packet.decode("utf-8"))
    return pnginfo


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
        if packet is None:
            packet = image.info.get(_PNG_XMP_KEY)
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
