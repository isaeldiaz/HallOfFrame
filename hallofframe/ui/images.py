"""One image loader for the whole UI (spec §7.2).

Every widget that shows a photo — crossing thumbnails, the last-capture panel,
the live preview and the review frame pane — goes through :func:`load_scaled`.
It decodes with ``QImageReader.setScaledSize()`` *before* ``read()`` so libjpeg
does the 1/2, 1/4 and 1/8 scaling in the DCT domain during decode, which is
roughly an order of magnitude cheaper than a full 1080p decode followed by a
downscale (spec §7.2). The residual resize uses ``Qt.FastTransformation`` unless
``fast=False``.

``roi`` is a normalised ``(x, y, w, h)`` crop applied *before* scaling. The
zoom/ROI nice-to-have (spec §13.3) keeps one process-wide view ROI: widgets that
must honour the operator's zoom call :func:`load_view` (which applies it); the
calibration dialog keeps calling :func:`load_scaled` so it always shows the full
frame. The crop path clips in the DCT domain (``setClipRect`` then
``setScaledSize``) and falls back to a full decode + :func:`_crop` when Qt's
reader does not honour the requested clip/scale within 2 px.
"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QRect, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

# The process-wide view ROI: None = full frame. Set by the Ready screen /
# MainWindow and read by every view that honours the zoom.
_view_roi: tuple[float, float, float, float] | None = None


def _clamp_roi(roi) -> tuple[float, float, float, float] | None:
    """Validate/clamp a normalised ROI: 0<=x,y, x+w<=1, y+h<=1, w>=0.05."""
    if roi is None:
        return None
    x, y, w, h = (float(v) for v in roi)
    w = min(1.0, max(0.05, w))
    h = min(1.0, max(0.05, h))
    x = min(max(0.0, x), 1.0 - w)
    y = min(max(0.0, y), 1.0 - h)
    return (x, y, w, h)


def set_view_roi(roi) -> None:
    """Set the process-wide view ROI (clamped); ``None`` = full frame."""
    global _view_roi
    _view_roi = _clamp_roi(roi)


def view_roi() -> tuple[float, float, float, float] | None:
    """The current process-wide view ROI, or None for the full frame."""
    return _view_roi


def load_view(source: str | bytes, size: QSize, *, fast: bool = True
              ) -> QPixmap | None:
    """:func:`load_scaled` with the current view ROI applied."""
    return load_scaled(source, size, fast=fast, roi=_view_roi)


def _reader_for(source):
    """Build a fresh reader for *source*; return ``(reader, keepalive)``.

    A ``QBuffer`` backing a bytes source must outlive the reader, so it is
    returned as the keepalive; for a path the keepalive is None."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        if not data:
            return None, None
        buf = QBuffer()
        buf.setData(data)
        buf.open(QBuffer.ReadOnly)
        return QImageReader(buf), buf
    path = str(source)
    if not path:
        return None, None
    return QImageReader(path), None


def _clip_rect(natural: QSize,
               roi: tuple[float, float, float, float]) -> QRect:
    """The ROI as a natural-pixel rect (rounded, clamped, min 1x1)."""
    x, y, w, h = roi
    iw, ih = natural.width(), natural.height()
    rx = max(0, min(iw - 1, int(round(x * iw))))
    ry = max(0, min(ih - 1, int(round(y * ih))))
    rw = max(1, min(iw - rx, int(round(w * iw))))
    rh = max(1, min(ih - ry, int(round(h * ih))))
    return QRect(rx, ry, rw, rh)


def _crop(img: QImage, roi: tuple[float, float, float, float]) -> QImage:
    """Normalised crop; clamps to the image bounds and never returns empty."""
    x, y, w, h = roi
    iw, ih = img.width(), img.height()
    rx = max(0, min(iw - 1, int(round(x * iw))))
    ry = max(0, min(ih - 1, int(round(y * ih))))
    rw = max(1, min(iw - rx, int(round(w * iw))))
    rh = max(1, min(ih - ry, int(round(h * ih))))
    return img.copy(rx, ry, rw, rh)


def load_scaled(source: str | bytes, size: QSize, *, fast: bool = True,
                roi: tuple[float, float, float, float] | None = None
                ) -> QPixmap | None:
    """Decode *source* (a path or JPEG bytes) into a *size*-fitted QPixmap.

    Returns ``None`` when the source is empty or unreadable. Aspect ratio is
    preserved, so the result fits inside *size* (letterboxing is the caller's
    job). ``roi`` crops first; ``fast=False`` uses smooth scaling for the
    residual resize.
    """
    if source is None:
        return None
    reader, _keep = _reader_for(source)
    if reader is None or not reader.canRead():
        return None

    transform = Qt.FastTransformation if fast else Qt.SmoothTransformation

    if roi is not None:
        natural = reader.size()
        if natural.isValid() and size.isValid():
            clip = _clip_rect(natural, roi)
            target = clip.size().scaled(size, Qt.KeepAspectRatio)
            # Qt applies clipRect in natural pixels before scaledSize, so the
            # crop is done in the DCT domain (§7.2). Verify it did.
            reader.setClipRect(clip)
            reader.setScaledSize(target)
            img = reader.read()
            if (not img.isNull()
                    and abs(img.width() - target.width()) <= 2
                    and abs(img.height() - target.height()) <= 2):
                if img.size() != target:
                    img = img.scaled(target, Qt.KeepAspectRatio, transform)
                return QPixmap.fromImage(img)
        # Fallback: the reader ignored the clip/scale. Full decode, crop, scale.
        reader2, _keep2 = _reader_for(source)
        img = reader2.read() if reader2 is not None else QImage()
        if img.isNull():
            return None
        img = _crop(img, roi)
        if size.isValid() and img.size() != size:
            target = img.size().scaled(size, Qt.KeepAspectRatio)
            img = img.scaled(target, Qt.KeepAspectRatio, transform)
        return QPixmap.fromImage(img)

    natural = reader.size()
    target = (natural.scaled(size, Qt.KeepAspectRatio)
              if natural.isValid() and size.isValid() else natural)
    if target.isValid():
        reader.setScaledSize(target)
    img = reader.read()
    if img.isNull():
        return None
    if target.isValid() and img.size() != target:
        img = img.scaled(target, Qt.KeepAspectRatio, transform)
    return QPixmap.fromImage(img)
