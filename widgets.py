# -*- coding: utf-8 -*-
"""自定义控件：VideoView（原视频 + 比例锁定裁剪框）、TimelineView（可拖动时间轴）。"""
import cv2

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import QApplication, QWidget


def bgr_to_qimage(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return qimg.copy()


class VideoView(QWidget):
    """显示原视频并在其上叠加可拖动、比例锁定的裁剪框。"""

    boxChanged = Signal(int, int, int)  # x, y, w（源像素坐标，用户改框后发出）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None      # QPixmap（原分辨率）
        self._src_w = 0
        self._src_h = 0
        self._box = None         # (x, y, w, h) 源坐标
        self.rw, self.rh = 16, 9
        self.interactive = False  # 仅高光帧允许拖动

        self._mode = None         # None / 'move' / 'resize'
        self._corner = None
        self._start_box = None
        self._press_src = None
        self._fixed = None        # 缩放时固定对角 (fx, fy) 源坐标
        self._handle = 14

    # ---- 数据设置 ----
    def set_frame(self, bgr):
        if bgr is None:
            self._pixmap = None
            self._src_w = self._src_h = 0
        else:
            self._src_h, self._src_w = bgr.shape[:2]
            # 显示前降采样（原图可能 1080p+，整幅转 QImage 会卡顿）
            cap = 1280
            if max(self._src_w, self._src_h) > cap:
                s = cap / max(self._src_w, self._src_h)
                bgr = cv2.resize(bgr, (int(round(self._src_w * s)), int(round(self._src_h * s))),
                                 interpolation=cv2.INTER_AREA)
            self._pixmap = QPixmap.fromImage(bgr_to_qimage(bgr))
        self.update()

    def set_box(self, box):
        self._box = box
        self.update()

    def set_ratio(self, rw, rh):
        self.rw, self.rh = rw, rh
        self.update()

    def reset_interaction(self):
        self._mode = None

    # ---- 坐标换算 ----
    def _scale_offsets(self):
        w = self.width(); h = self.height()
        if not self._src_w or not self._src_h:
            return 1.0, 0, 0
        s = min(w / self._src_w, h / self._src_h)
        ox = (w - self._src_w * s) / 2
        oy = (h - self._src_h * s) / 2
        return s, ox, oy

    def _to_src(self, px, py):
        s, ox, oy = self._scale_offsets()
        return (px - ox) / s, (py - oy) / s

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(15, 16, 18))
        if self._pixmap is None:
            p.setPen(QColor(150, 154, 158))
            p.drawText(self.rect(), Qt.AlignCenter, "无视频")
            return

        s, ox, oy = self._scale_offsets()
        sw = self._src_w * s
        sh = self._src_h * s
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        p.drawPixmap(QRectF(ox, oy, sw, sh), self._pixmap, QRectF(0, 0, pw, ph))

        if self._box is None:
            return
        x, y, w, h = self._box
        wx, wy, ww, wh = ox + x * s, oy + y * s, w * s, h * s

        # 框外压暗
        dim = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(ox, oy, sw, wy - oy), dim)                              # top
        p.fillRect(QRectF(ox, wy + wh, sw, (oy + sh) - (wy + wh)), dim)           # bottom
        p.fillRect(QRectF(ox, wy, wx - ox, wh), dim)                              # left
        p.fillRect(QRectF(wx + ww, wy, (ox + sw) - (wx + ww), wh), dim)           # right

        # 绿色框
        pen = QPen(QColor(0, 210, 90), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(wx, wy, ww, wh))

        # 四个角 + 右下角手柄
        p.setBrush(QColor(0, 210, 90))
        r = 5
        for cx, cy in ((wx, wy), (wx + ww, wy), (wx, wy + wh), (wx + ww, wy + wh)):
            p.drawRect(QRectF(cx - r / 2, cy - r / 2, r, r))
        p.drawRect(QRectF(wx + ww - self._handle / 2, wy + wh - self._handle / 2,
                          self._handle, self._handle))

    # ---- 交互 ----
    def mousePressEvent(self, e):
        if self._box is None or not self.interactive or e.button() != Qt.LeftButton:
            return
        x, y, w, h = self._box
        s, ox, oy = self._scale_offsets()
        wx, wy, ww, wh = ox + x * s, oy + y * s, w * s, h * s
        px, py = e.position().x(), e.position().y()

        corners = {"tl": (wx, wy), "tr": (wx + ww, wy),
                   "bl": (wx, wy + wh), "br": (wx + ww, wy + wh)}
        for name, (cx, cy) in corners.items():
            if abs(px - cx) <= 14 and abs(py - cy) <= 14:
                self._mode = "resize"
                self._corner = name
                self._fixed = self._fixed_corner(x, y, w, h, name)
                self._start_box = (x, y, w, h)
                return
        if wx <= px <= wx + ww and wy <= py <= wy + wh:
            self._mode = "move"
            self._start_box = (x, y, w, h)
            self._press_src = self._to_src(px, py)
            return
        self._mode = None

    def _fixed_corner(self, x, y, w, h, name):
        if name == "br":
            return (x, y)
        if name == "bl":
            return (x + w, y)
        if name == "tr":
            return (x, y + h)
        return (x + w, y + h)  # tl

    def mouseMoveEvent(self, e):
        if self._mode is None or self._box is None:
            return
        x, y, w, h = self._start_box
        mx, my = self._to_src(e.position().x(), e.position().y())
        if self._mode == "move":
            dx = mx - self._press_src[0]
            dy = my - self._press_src[1]
            nx = x + round(dx)
            ny = y + round(dy)
            nx, ny, w, h = self._clamp(nx, ny, w, h)
            self._box = (nx, ny, w, h)
        elif self._mode == "resize":
            self._box = self._resize_box(mx, my)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._mode is not None and self._box is not None:
            x, y, w, h = self._box
            self._mode = None
            self.boxChanged.emit(int(x), int(y), int(w))
        self._mode = None

    def _resize_box(self, mx, my):
        fx, fy = self._fixed
        r = self.rw / self.rh
        dx = mx - fx
        dy = my - fy
        w = max(abs(dx), abs(dy) * r)
        w = max(1.0, w)
        w = min(w, self._src_w, self._src_h * r)
        w = max(1.0, w)
        h = w / r
        x = fx if dx >= 0 else fx - w
        y = fy if dy >= 0 else fy - h
        return self._clamp(x, y, w, h)

    def _clamp(self, x, y, w, h):
        w = max(1.0, min(float(w), self._src_w))
        h = max(1.0, min(float(h), self._src_h))
        x = max(0.0, min(float(x), self._src_w - w))
        y = max(0.0, min(float(y), self._src_h - h))
        return x, y, w, h


class TimelineView(QWidget):
    """时间轴：定位播放头、移动高光段和调整片段边界。"""

    seekRequested = Signal(int)
    segmentClicked = Signal(int)
    segmentDragStarted = Signal()
    segmentEdited = Signal(int, str, int, int, int)  # index, mode, start, end, playhead

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0
        self.fps = 30.0
        self.segments = []       # [(start, end), ...]
        self.keyframes = []      # [frame, ...] 关键帧帧号
        self.playhead = 0
        self.selected = -1
        self._scrubbing = False
        self._drag = None  # (index, mode, press_x, press_frame)
        self._drag_range = None
        self.setMinimumHeight(92)
        self.setMouseTracking(True)
        self.setToolTip("拖动高光两端调整起止帧，拖动中间移动整段。拖动刻度或空白处定位。")

    def set_data(self, total, fps, segments, keyframes, playhead, selected):
        if self.total != total or self.segments != segments:
            self.reset_interaction()
        self.total = total
        self.fps = fps if fps else 30.0
        self.segments = segments
        self.keyframes = keyframes
        self.playhead = playhead
        self.selected = selected
        self.update()

    def reset_interaction(self):
        self._scrubbing = False
        self._drag = None
        self._drag_range = None
        self.unsetCursor()
        self.update()

    def _bar(self):
        pad = 14
        return pad, max(1, self.width() - 2 * pad), 40, self.height() - 40 - 26

    def _x(self, frame):
        bx, bw, by, bh = self._bar()
        return bx + (frame / max(1, self.total)) * bw

    def _frame_at(self, px):
        bx, bw, by, bh = self._bar()
        f = (px - bx) / bw * self.total
        return int(round(max(0, min(self.total - 1, f))))

    def _segment_rect(self, start, end):
        _, _, by, bh = self._bar()
        left = self._x(start)
        return QRectF(left, by, self._x(end + 1) - left, bh)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(28, 30, 34))
        bx, bw, by, bh = self._bar()

        # 底条
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(60, 62, 66))
        p.drawRect(bx, by, bw, bh)

        # 高光段
        for i, (s, e) in enumerate(self.segments):
            if s >= self.total or e < 0:
                continue
            col = QColor(255, 170, 40) if i == self.selected else QColor(255, 138, 0)
            rect = self._segment_rect(max(s, 0), min(e, self.total - 1))
            p.fillRect(rect, col)
            if i == self.selected:
                handle = min(3, rect.width() / 4)
                for x in (rect.left(), rect.right() - handle):
                    p.fillRect(QRectF(x, by + bh / 4, handle, bh / 2), QColor(255, 230, 180))

        # 拖动期间预览目标区间，松手后才提交修改。
        if self._drag_range is not None:
            rect = self._segment_rect(*self._drag_range)
            p.fillRect(rect, QColor(255, 210, 140, 110))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 230, 180), 1, Qt.DashLine))
            p.drawRect(rect)

        # 关键帧标记（菱形）
        p.setPen(QPen(QColor(80, 200, 255), 2))
        for f in self.keyframes:
            cx = int(self._x(f))
            p.drawLine(cx - 5, by, cx, by - 6)
            p.drawLine(cx, by - 6, cx + 5, by)
            p.drawLine(cx + 5, by, cx, by + 6)
            p.drawLine(cx, by + 6, cx - 5, by)

        # 播放头
        last_frame = max(0, self.total - 1)
        px = self._x(min(self.playhead, last_frame))
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(int(px), by - 6, int(px), by + bh + 6)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(px - 4, by - 10, 8, 8))

        # 时间刻度
        p.setPen(QColor(170, 174, 178))
        dur = self.total / self.fps
        t = 0.0
        while t <= dur + 1e-6:
            xx = self._x(int(t * self.fps))
            p.drawLine(int(xx), by + bh, int(xx), by + bh + 4)
            mm, ss = int(t // 60), int(t % 60)
            p.drawText(int(xx - 12), by + bh + 18, f"{mm}:{ss:02d}")
            t += 10.0

        # 当前时间
        cur = self.playhead / self.fps
        mm, ss = int(cur // 60), int(cur % 60)
        label = f"t = {mm}:{ss:02d}  帧 {self.playhead}/{last_frame}"
        if self._drag_range is not None:
            s, e = self._drag_range
            label += f"  ·  高光 {s}–{e} 帧"
        p.drawText(bx, by - 12, label)

    def _segment_hit(self, px, py):
        _, _, by, bh = self._bar()
        if not by <= py < by + bh:
            return None
        for i, (s, e) in enumerate(self.segments):
            if not 0 <= s <= e < self.total:
                continue
            rect = self._segment_rect(s, e)
            if rect.left() <= px < rect.right():
                edge = min(7, rect.width() / 3)
                if px - rect.left() < edge:
                    return i, "start"
                if rect.right() - px <= edge:
                    return i, "end"
                return i, "move"
        return None

    def _update_cursor(self, hit, dragging=False):
        if hit is None:
            self.unsetCursor()
        elif hit[1] == "move":
            self.setCursor(Qt.ClosedHandCursor if dragging else Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.SizeHorCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.total <= 0:
            return
        self.reset_interaction()
        px, py = e.position().x(), e.position().y()
        frame = self._frame_at(px)
        hit = self._segment_hit(px, py)
        if hit is None:
            self._scrubbing = True
            self._seek(frame)
            return
        index, mode = hit
        start, end = self.segments[index]
        if mode == "start":
            frame = start
        elif mode == "end":
            frame = end
        else:
            frame = min(end, max(start, frame))
        self._drag = (index, mode, px, frame)
        self._update_cursor(hit)
        self._seek(frame, index)

    def mouseMoveEvent(self, e):
        px, py = e.position().x(), e.position().y()
        if e.buttons() & Qt.LeftButton:
            if self._drag is not None:
                self._preview_drag(px)
            elif self._scrubbing:
                self._seek(self._frame_at(px))
        else:
            self._update_cursor(self._segment_hit(px, py))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        px, py = e.position().x(), e.position().y()
        if self._drag is not None:
            self._preview_drag(px)
            index, mode, _, frame = self._drag
            proposed = self._drag_range
            old_start, old_end = self.segments[index]
            self.reset_interaction()
            if proposed is not None and proposed != (old_start, old_end):
                start, end = proposed
                playhead = end if mode == "end" else frame + start - old_start
                self.segmentEdited.emit(index, mode, start, end, playhead)
        elif self._scrubbing:
            self.reset_interaction()
            frame = self._frame_at(px)
            if frame != self.playhead:
                self._seek(frame)
        self._update_cursor(self._segment_hit(px, py))

    def _preview_drag(self, px):
        index, mode, press_x, _ = self._drag
        distance = px - press_x
        _, width, _, _ = self._bar()
        delta = round(distance * self.total / width)
        if self._drag_range is None:
            if delta == 0 or (mode == "move" and abs(distance) < QApplication.startDragDistance()):
                return
            self.segmentDragStarted.emit()
            self._update_cursor((index, mode), dragging=True)

        start, end = self.segments[index]
        lower, upper = 0, self.total - 1
        # 允许编辑已有的重叠段，但不因拖动增加与其他段的重叠。
        for other, (s, e) in enumerate(self.segments):
            if other == index:
                continue
            if s < start:
                lower = max(lower, min(start, e + 1))
            if e > end:
                upper = min(upper, max(end, s - 1))
        if mode == "start":
            start = min(end, max(lower, start + delta))
        elif mode == "end":
            end = max(start, min(upper, end + delta))
        else:
            delta = max(lower - start, min(upper - end, delta))
            start, end = start + delta, end + delta
        self._drag_range = (start, end)
        self.update()

    def _seek(self, frame, index=None):
        if index is None:
            index = next((i for i, (s, e) in enumerate(self.segments) if s <= frame <= e), -1)
        if index != self.selected:
            self.selected = index
            self.segmentClicked.emit(index)
        self.seekRequested.emit(frame)
