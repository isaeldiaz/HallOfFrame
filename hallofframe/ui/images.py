"""One image loader for the whole UI (spec §7.2).

Every widget that shows a photo — crossing thumbnails, the last-capture panel,
the live preview and the review frame pane — goes through :func:`load_scaled`.
It decodes with ``QImageReader.setScaledSize()`` *before* ``read()`` so libjpeg
does the 1/2, 1/4 and 1/8 scaling in the DCT domain during decode, which is
roughly an order of magnitude cheaper than a full 1080p decode followed by a
downscale (spec §7.2). The residual resize uses ``Qt.FastTransformation`` unless
``fast=False``.

``roi`` is a normalised ``(x, y, w, h)`` crop applied *before* scaling; it is the
hook the deferred zoom/ROI nice-to-have (§13.3) needs and is ``None`` everywhere
in this phase. With ``roi=None`` the behaviour is the pre-existing loader's:
decode at the requested size, keeping the aspect ratio.
"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap


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
    buf = None
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        if not data:
            return None
        buf = QBuffer()
        buf.setData(data)
        buf.open(QBuffer.ReadOnly)
        reader = QImageReader(buf)
    else:
        path = str(source)
        if not path:
            return None
        reader = QImageReader(path)
    if not reader.canRead():
        return None

    transform = Qt.FastTransformation if fast else Qt.SmoothTransformation

    if roi is not None:
        img = reader.read()
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
