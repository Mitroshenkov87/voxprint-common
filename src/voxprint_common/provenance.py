"""Provenance stub.

A later version embeds an invisible watermark and writes C2PA and IPTC
metadata after the app's last processing step, and only when the user has
left the watermark on. This version defines that API and does not modify
files. There is no regional default and no extra confirmation: the settings
object is the user's choice, with the watermark on and the visible label off.

The visible label, the watermark, and the metadata travel together. When the
watermark is off, a later version writes none of them and does not re-sign a
manifest it found on the input.

Payload, when that version lands: marker ``VX``, a schema nibble, an app id,
a year-month, reserved bits, and a CRC. The payload has no user name, machine
id, or network address. ``detect`` reports that a missing mark does not prove
a file was made by a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "NO_MARK_NOTE",
    "ProvenanceContext",
    "ProvenanceSettings",
    "ProvenanceUnavailable",
    "Report",
    "detect",
    "embed_image",
    "finalize",
]

NO_MARK_NOTE = "no mark found does not prove the file is human-made"


class ProvenanceUnavailable(NotImplementedError):
    """Embedding and detection are not implemented in this version."""


@dataclass(frozen=True)
class ProvenanceSettings:
    """The user's labelling choice for one run.

    ``watermark`` covers the invisible watermark and the provenance metadata.
    ``label`` is the visible badge, spoken notice, or colophon. Defaults match
    the suite: watermark on, label off.
    """

    watermark: bool = True
    label: bool = False
    label_text: str = ""
    label_pos: str = "corner"


@dataclass(frozen=True)
class ProvenanceContext:
    """Who produced a file, and which IPTC digital source type applies."""

    app: str
    version: str
    build: int
    source_type: str
    action: str


@dataclass(frozen=True)
class Report:
    """What a later version of :func:`detect` will return.

    ``note`` is :data:`NO_MARK_NOTE` when nothing was found, so a detector
    does not treat silence as proof that a person made the file.
    """

    path: str
    c2pa_present: bool = False
    c2pa_valid: bool = False
    signer: str = ""
    digital_source_type: str = ""
    watermark_found: bool = False
    algorithm: str = ""
    confidence: float | None = None
    app: str = ""
    year_month: str = ""
    note: str = NO_MARK_NOTE


def embed_image(image: object, ctx: ProvenanceContext, settings: ProvenanceSettings) -> object:
    """Embed a watermark and an optional visible label on an image.

    A later version does this after the app's final processing step and before
    the encoder. This version does not read or modify ``image``.

    Args:
        image: The app's image object.
        ctx: App, version, build, source type, and C2PA action.
        settings: The user's watermark and label choice for this run.

    Returns:
        This version does not return.

    Raises:
        ProvenanceUnavailable: Always, in voxprint-common 0.1.
    """
    del image, ctx, settings
    raise ProvenanceUnavailable(
        "Image provenance embedding is not implemented in voxprint-common 0.1."
    )


def finalize(path: str | Path, ctx: ProvenanceContext, settings: ProvenanceSettings) -> None:
    """Write C2PA and IPTC metadata on a finished file.

    A later version writes them only when ``settings.watermark`` is true, and
    writes nothing when it is false.

    Args:
        path: File the app has already encoded.
        ctx: App, version, build, source type, and C2PA action.
        settings: The user's watermark and label choice for this run.

    Raises:
        ProvenanceUnavailable: Always, in voxprint-common 0.1.
    """
    del path, ctx, settings
    raise ProvenanceUnavailable("Provenance metadata is not implemented in voxprint-common 0.1.")


def detect(path: str | Path) -> Report:
    """Read a watermark and provenance metadata from a file.

    A later version returns a :class:`Report`. This version does not read the
    file.

    Args:
        path: Media file to inspect.

    Returns:
        This version does not return.

    Raises:
        ProvenanceUnavailable: Always, in voxprint-common 0.1.
    """
    del path
    raise ProvenanceUnavailable("Provenance detection is not implemented in voxprint-common 0.1.")
