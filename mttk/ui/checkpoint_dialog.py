"""Screen checkpoint editor and the "checkpoint failed" window."""
from __future__ import annotations

import copy
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QValidator
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .. import notify
from .. import vision as V
from . import theme
from .timeline import fmt_time, parse_time

DELETE = 2  # dialog result: delete this checkpoint


class TimeSpin(QDoubleSpinBox):
    """A time in the macro, shown and typed as 1:34.50 (or 94.5)."""

    def __init__(self, value: float, maximum: float, parent=None):
        super().__init__(parent)
        self.setDecimals(3)
        self.setRange(0.0, max(maximum, value))
        self.setSingleStep(0.1)
        self.setValue(value)
        self.setMinimumWidth(110)

    def textFromValue(self, v: float) -> str:
        return fmt_time(v, 2)

    def valueFromText(self, text: str) -> float:
        v = parse_time(text)
        return self.value() if v is None else v

    def validate(self, text: str, pos: int):
        v = parse_time(text)
        ok = v is not None and v <= self.maximum() + 1e-9
        return QValidator.Acceptable if ok else QValidator.Intermediate, text, pos

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.value())


class _Relay(QObject):
    """Brings the result of a test message back to the GUI thread."""
    done = Signal(bool, str)


class RegionPicker(QWidget):
    """Shows the checkpoint screenshot; drag on it to choose the area to compare."""
    changed = Signal()

    def __init__(self, image: QImage, image_rect, region, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._drag = None
        self.set_image(image, image_rect, region)

    def set_image(self, image: QImage, image_rect, region=None):
        self.image = image
        self.pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self.image_rect = list(image_rect)
        self.region = V.clamp_region(region or image_rect, self.image_rect)
        self.update()
        self.changed.emit()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(760, 428)

    # -- coordinates: desktop pixels <-> widget
    def _view(self) -> QRectF:
        iw, ih = self.image_rect[2], self.image_rect[3]
        w, h = self.width() - 2, self.height() - 2
        f = min(w / max(1, iw), h / max(1, ih))
        vw, vh = iw * f, ih * f
        return QRectF(1 + (w - vw) / 2, 1 + (h - vh) / 2, vw, vh)

    def _to_view(self, rect) -> QRectF:
        v = self._view()
        ix, iy, iw, ih = self.image_rect
        fx, fy = v.width() / max(1, iw), v.height() / max(1, ih)
        x, y, w, h = rect
        return QRectF(v.x() + (x - ix) * fx, v.y() + (y - iy) * fy, w * fx, h * fy)

    def _to_desktop(self, pos) -> tuple:
        v = self._view()
        ix, iy, iw, ih = self.image_rect
        x = ix + (pos.x() - v.x()) * iw / max(1.0, v.width())
        y = iy + (pos.y() - v.y()) * ih / max(1.0, v.height())
        return min(max(x, ix), ix + iw), min(max(y, iy), iy + ih)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE_2))
        v = self._view()
        if not self.pixmap.isNull():
            p.drawPixmap(v, self.pixmap, QRectF(self.pixmap.rect()))
        else:
            p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(v, Qt.AlignCenter, "No screenshot")
        r = self._to_view(self.region)
        shade = QPainterPath()
        shade.addRect(v)
        inner = QPainterPath()
        inner.addRect(r)
        p.fillPath(shade.subtracted(inner), QColor(0, 0, 0, 140))
        p.setPen(QPen(QColor(theme.GREEN), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        label = f"{self.region[2]} × {self.region[3]} px"
        p.setPen(QColor("white"))
        tr = QRectF(r.x() + 6, r.y() + 4, 200, 18)
        p.fillRect(QRectF(r.x() + 2, r.y() + 2, p.fontMetrics().horizontalAdvance(label) + 10, 20),
                   QColor(0, 0, 0, 150))
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, label)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = self._to_desktop(e.position())

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        x0, y0 = self._drag
        x1, y1 = self._to_desktop(e.position())
        if abs(x1 - x0) >= 4 and abs(y1 - y0) >= 4:
            self.region = V.clamp_region([round(min(x0, x1)), round(min(y0, y1)),
                                          round(abs(x1 - x0)), round(abs(y1 - y0))], self.image_rect)
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None


def _scaled(img: Optional[QImage], w: int, h: int) -> QPixmap:
    if img is None or img.isNull():
        return QPixmap()
    return QPixmap.fromImage(img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class CheckpointDialog(QDialog):
    """Edit one checkpoint: name, time, area, what it does, how strict, how long to wait.

    ``grab(rect)`` takes a screenshot without our own windows in it and
    ``to_screen(rect, screen)`` maps checkpoint coordinates to this screen.
    ``max_time`` is the latest time the checkpoint can be moved to and
    ``notify_cfg`` the phone alert settings (for "send a test picture").
    """

    def __init__(self, spec: dict, at_time: float, grab: Callable, to_screen: Callable,
                 parent=None, is_new: bool = False, max_time: Optional[float] = None,
                 notify_cfg: Optional[dict] = None):
        super().__init__(parent)
        self.spec = copy.deepcopy(spec)
        photo = not V.compares(self.spec)
        if is_new:
            self.setWindowTitle("New screenshot point" if photo else "New screen checkpoint")
        else:
            self.setWindowTitle("Screen checkpoint")
        self._grab_screen = grab
        self.to_screen = to_screen
        self._at_time = at_time
        self._notify_cfg = dict(notify_cfg or {})
        self._relay = _Relay(self)
        self._relay.done.connect(self._test_sent)
        self.setMinimumWidth(820)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.name = QLineEdit(self.spec.get("name", "Checkpoint"))
        self.name.setPlaceholderText("Name, e.g. Shop window open")
        top.addWidget(QLabel("Name"))
        top.addWidget(self.name, 1)
        top.addSpacing(8)
        top.addWidget(QLabel("at"))
        self.time = TimeSpin(at_time, at_time if max_time is None else max_time)
        self.time.setToolTip("When in the macro this happens. Type a time like 1:34 or 94.5.")
        top.addWidget(self.time)
        when = QLabel("in the macro")
        when.setObjectName("Dim")
        top.addWidget(when)
        lay.addLayout(top)

        img = V.decode_image(self.spec.get("image", ""))
        self.picker = RegionPicker(img, self.spec["image_rect"], self.spec.get("region"))
        self.picker.changed.connect(self._region_changed)
        lay.addWidget(self.picker, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        b = QPushButton("Whole screen")
        b.clicked.connect(lambda: self.picker.set_image(self.picker.image, self.picker.image_rect,
                                                        self.picker.image_rect))
        row.addWidget(b)
        b = QPushButton("Retake screenshot")
        b.setToolTip("Take the picture again from the screen as it is now (this window is left out).")
        b.clicked.connect(self._retake)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.compare = QCheckBox("Compare the screen, and stop the macro if it doesn't look like this")
        self.compare.setChecked(V.compares(self.spec))
        self.compare.setToolTip("Off: the macro never pauses or stops here (use it to just send a picture).")
        self.send = QCheckBox("Send a picture of the screen to your phone (Discord, Telegram or ntfy)")
        self.send.setChecked(V.sends(self.spec))
        self.send_note = QLabel("")
        self.send_note.setObjectName("Faint")
        self.send_note.setWordWrap(True)
        what = QWidget()
        wv = QVBoxLayout(what)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(4)
        wv.addWidget(self.compare)
        wv.addWidget(self.send)
        wv.addWidget(self.send_note)
        form.addRow("When the macro gets here", what)
        self.threshold = QSpinBox()
        self.threshold.setRange(10, 100)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(int(self.spec.get("threshold", V.DEFAULT_THRESHOLD)))
        self.threshold.setToolTip("How much of the area must look the same. 100% = identical; lower it if\n"
                                  "popups or small changes are normal here. 80% suits most cases.")
        form.addRow("Required match", self._with_note(self.threshold, "of the area must look the same"))
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0, 600)
        self.timeout.setDecimals(1)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(float(self.spec.get("timeout", V.DEFAULT_TIMEOUT)))
        self.timeout.setToolTip("Playback pauses here and keeps checking for this long, so a laggy screen\n"
                                "can catch up. Everything after the checkpoint waits too.")
        form.addRow("Wait up to", self._with_note(self.timeout, "for it to match (the macro pauses meanwhile)"))
        self.shift = QSpinBox()
        self.shift.setRange(0, 1000)
        self.shift.setSuffix(" px")
        self.shift.setValue(int(self.spec.get("shift", V.DEFAULT_SHIFT)))
        self.shift.setToolTip("0 = the picture must be in exactly the same place (catches a view that drifted).\n"
                              "Allow a few pixels if the window may sit slightly differently.")
        form.addRow("Allowed shift", self._with_note(self.shift, "the picture may be off by"))
        lay.addLayout(form)

        test = QHBoxLayout()
        self.test_btn = QPushButton("Test against the screen now")
        self.test_btn.clicked.connect(self._test)
        test.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test.addWidget(self.test_result, 1)
        lay.addLayout(test)
        self.compare.toggled.connect(self._mode_changed)
        self.send.toggled.connect(self._mode_changed)
        self._mode_changed()

        buttons = QHBoxLayout()
        if not is_new:
            d = QPushButton("Delete checkpoint")
            d.setStyleSheet(f"QPushButton {{ color: {theme.RED}; }}")
            d.clicked.connect(lambda: self.done(DELETE))
            buttons.addWidget(d)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(("Add screenshot point" if photo else "Add checkpoint") if is_new else "OK")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)

    @staticmethod
    def _with_note(widget, note):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(widget)
        lbl = QLabel(note)
        lbl.setObjectName("Faint")
        h.addWidget(lbl)
        h.addStretch(1)
        return w

    def _region_changed(self):
        self.test_result.setText("")

    def _mode_changed(self, *_):
        compare, send = self.compare.isChecked(), self.send.isChecked()
        for w in (self.threshold, self.timeout, self.shift):
            w.setEnabled(compare)
        if compare:
            self.hint.setText("Drag over the part of the screen that must look like this when the macro gets "
                              "here. Leave out things that change on their own (chat, timers, animations).")
            self.test_btn.setText("Test against the screen now")
            self.test_btn.setToolTip("Compare the area with what is on screen right now (this window is left out).")
        else:
            self.hint.setText("Drag over the part of the screen to send (Whole screen sends the whole monitor). "
                              "The macro doesn't pause or stop here.")
            self.test_btn.setText("Send a test picture now")
            self.test_btn.setToolTip("Send the area as it is on screen right now (this window is left out).")
        problem = notify.problem(self._notify_cfg) if self._notify_cfg.get("service", "none") != "none" \
            else "Phone alerts are off"
        if not compare and not send:
            note = f"<span style='color:{theme.YELLOW}'>With both off, this point does nothing.</span>"
        elif send and problem:
            note = (f"<span style='color:{theme.YELLOW}'>{problem}: set them up in Settings → Checkpoints "
                    "&amp; alerts, or nothing is sent.</span>")
        elif send and compare:
            note = "Sent when the screen matches: the whole monitor with the area outlined."
        elif send:
            note = "The area above is sent every time playback gets here."
        else:
            note = ""
        self.send_note.setText(note)
        self.send_note.setVisible(bool(note))
        self.test_btn.setEnabled(compare or not problem)
        self.test_result.setText("")

    def _retake(self):
        # the picture's area is in the macro's coordinates: take it where it is on screen now
        img = self._grab_screen(self.to_screen(self.picker.image_rect, self.spec.get("screen")))
        if img is None or img.isNull():
            self.test_result.setText("Couldn't take a screenshot.")
            return
        self.spec["image"] = V.encode_image(img)
        self.picker.set_image(V.decode_image(self.spec["image"]), self.picker.image_rect, self.picker.region)
        self.test_result.setText("Screenshot retaken.")

    def result_spec(self) -> dict:
        s = self.spec
        s["name"] = self.name.text().strip() or "Checkpoint"
        s["region"] = list(self.picker.region)
        s["threshold"] = self.threshold.value()
        s["timeout"] = self.timeout.value()
        s["shift"] = self.shift.value()
        if self.compare.isChecked():
            s.pop("compare", None)   # the default: older versions read it the same way
        else:
            s["compare"] = False
        if self.send.isChecked():
            s["send"] = True
        else:
            s.pop("send", None)
        return s

    def result_time(self) -> float:
        """The (possibly edited) time of the checkpoint in the macro."""
        v = self.time.value()
        # untouched: keep the exact original time (the spin box rounds to milliseconds)
        return self._at_time if abs(v - round(self._at_time, 3)) < 1e-9 else v

    def _test(self):
        if not self.compare.isChecked():
            self._send_test_picture()
            return
        spec = dict(self.result_spec(), timeout=0.0)
        spec.pop("send", None)   # just the comparison, no picture to send
        results = []
        runner = V.CheckpointRunner({"t": spec}, to_screen=self.to_screen, capture=self._grab_screen,
                                    on_result=results.append)
        if runner.prepare(["t"]):
            self.test_result.setText("This checkpoint has no usable picture.")
            return
        runner.check("t")
        if not results:
            return
        res = results[-1]
        if res.error:
            self.test_result.setText(f"<span style='color:{theme.RED}'>{res.error}</span>")
        elif res.passed:
            self.test_result.setText(f"<span style='color:{theme.GREEN}'>✓ {res.score * 100:.0f}% match: "
                                     f"would pass (needs {res.threshold * 100:.0f}%)</span>")
        else:
            self.test_result.setText(f"<span style='color:{theme.RED}'>✗ {res.summary}: would stop the macro"
                                     "</span>")

    def _send_test_picture(self):
        img = self._grab_screen(self.to_screen(self.picker.region, self.spec.get("screen")))
        if img is None or img.isNull():
            self.test_result.setText(f"<span style='color:{theme.RED}'>Couldn't take a screenshot.</span>")
            return
        try:
            data = V.image_bytes(img)
        except Exception as exc:
            self.test_result.setText(f"<span style='color:{theme.RED}'>{exc}</span>")
            return
        name = self.name.text().strip() or "Screenshot"
        self.test_btn.setEnabled(False)
        self.test_result.setText("Sending…")
        notify.send_async(self._notify_cfg, f"\U0001f4f7 {name} (test)",
                          f"Test picture: this is what is sent at {fmt_time(self.time.value(), 2)} in the macro.",
                          data, priority="default", tags="", on_done=self._relay.done.emit)

    def _test_sent(self, ok: bool, error: str):
        self.test_btn.setEnabled(True)
        if ok:
            self.test_result.setText(f"<span style='color:{theme.GREEN}'>✓ Sent. Check your phone.</span>")
        else:
            self.test_result.setText(f"<span style='color:{theme.RED}'>⚠ {error}</span>")


class CheckFailedDialog(QDialog):
    """Shown when playback stopped because a checkpoint didn't match."""
    showOnTimeline = Signal()
    editCheckpoint = Signal()

    def __init__(self, res: V.CheckResult, when: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Macro stopped: checkpoint failed")
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        head = QLabel(f"<span style='font-size:13pt; font-weight:600; color:{theme.RED}'>■ Stopped at "
                      f"“{res.name}”</span>")
        lay.addWidget(head)
        info = QLabel(f"{res.summary}<br><span style='color:{theme.TEXT_DIM}'>{when}. "
                      "Keys and mouse buttons were released.</span>")
        info.setWordWrap(True)
        lay.addWidget(info)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for col, (title, img) in enumerate((("EXPECTED", res.expected), ("ON SCREEN", res.actual))):
            t = QLabel(title)
            t.setObjectName("CardTitle")
            grid.addWidget(t, 0, col)
            pic = QLabel()
            pic.setAlignment(Qt.AlignCenter)
            pic.setMinimumSize(340, 200)
            pic.setStyleSheet(f"background: {theme.SURFACE_2}; border-radius: 8px;")
            pm = _scaled(img, 360, 240)
            if pm.isNull():
                pic.setText("—")
            else:
                pic.setPixmap(pm)
            grid.addWidget(pic, 1, col)
        lay.addLayout(grid)

        self.alert = QLabel("")
        self.alert.setObjectName("Dim")
        self.alert.setWordWrap(True)
        lay.addWidget(self.alert)

        row = QHBoxLayout()
        b = QPushButton("Show on timeline")
        b.clicked.connect(lambda: (self.showOnTimeline.emit(), self.close()))
        row.addWidget(b)
        b = QPushButton("Edit checkpoint…")
        b.clicked.connect(lambda: (self.close(), self.editCheckpoint.emit()))
        row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.setDefault(True)
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

    def set_alert_text(self, text: str):
        self.alert.setText(text)
