# -*- coding: utf-8 -*-
"""离屏集成测试：恢复、拖动、撤回并重新导出已有标注。"""
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from config import Config, load_saved_annotation, scan_videos
from exporter import build_line, save_video_json
from main_window import MainWindow
from model import Keyframe, Segment, segments_to_predictions


def save_and_wait(win):
    with patch.object(QMessageBox, "critical") as errors:
        win.btn_save.click()
        deadline = monotonic() + 15
        while win._prog is not None and monotonic() < deadline:
            QApplication.processEvents()
            sleep(0.02)
        assert win._prog is None, "导出超时"
        assert not errors.called, errors.call_args


def drag_timeline(timeline, start_x, end_x, y):
    before = list(timeline.segments)
    start = QPoint(round(start_x), y)
    end = QPoint(round(end_x), y)
    QTest.mousePress(timeline, Qt.LeftButton, pos=start)
    QTest.mouseMove(timeline, end)
    assert timeline.segments == before
    QTest.mouseRelease(timeline, Qt.LeftButton, pos=end)


def drag_segment(timeline, index, mode, frames):
    s, e = timeline.segments[index]
    rect = timeline._segment_rect(s, e)
    x = {"start": rect.left() + 2, "end": rect.right() - 2, "move": rect.center().x()}[mode]
    drag_timeline(timeline, x, x + timeline._x(frames) - timeline._x(0), round(rect.center().y()))


def press_undo(win):
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)


def check_edit_saved_annotations(tmp):
    source = tmp / "video"
    output = tmp / "output"
    ratios = tmp / "jsonl"
    source.mkdir()
    output.mkdir()
    ratios.mkdir()
    vid_path = source / "0.mp4"
    writer = cv2.VideoWriter(str(vid_path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (160, 90))
    assert writer.isOpened()
    for i in range(60):
        frame = np.full((90, 160, 3), (i * 4 % 255, 90, 90), np.uint8)
        writer.write(frame)
    writer.release()
    for vid in (1, 2, 3):
        shutil.copyfile(vid_path, source / f"{vid}.mp4")

    segments = [
        Segment(10, 40, [Keyframe(10, 0, 20, 40), Keyframe(15, 38, 24, 44),
                         Keyframe(40, 40, 20, 40)]),
        Segment(42, 45, [Keyframe(42, 10, 20, 40)]),
    ]
    original = segments_to_predictions(segments, 1, 1)
    save_video_json(0, 1, 1, original, output)
    save_video_json(1, 16, 9, [], output)
    (output / "3.json").write_text("{", encoding="utf-8")
    (ratios / "0.jsonl").write_text('{"targetRatioWH":[16,9]}\n', encoding="utf-8")
    summary = output / "annotations.jsonl"
    old_row = build_line(0, 1, 1, original)
    other_row = build_line(1, 16, 9, [])
    summary.write_text("".join(json.dumps(row) + "\n" for row in (old_row, other_row, old_row)),
                       encoding="utf-8")
    prefill = {vid: build_line(vid, 4, 3, [{"frame": 7, "bboxes": [0, 0, 40]}])
               for vid in (0, 1, 2)}

    cfg = Config(source_dir=str(source), ratio_dir=str(ratios), out_dir=str(output))
    win = MainWindow(cfg, scan_videos(source), prefill)
    try:
        win.show()
        QApplication.processEvents()
        assert all(b.text() != "清空标注" for b in win.findChildren(QPushButton))
        assert win.vid == 2 and win.timeline.segments == [(7, 7)]
        assert (win.rw, win.rh) == (4, 3)
        assert win.reader.read(30).shape == (90, 160, 3)
        win.sidebar.itemClicked.emit(win.sidebar.item(1))
        assert win.vid == 1 and not win.segments
        with patch.object(QMessageBox, "critical") as error:
            win.sidebar.itemClicked.emit(win.sidebar.item(3))
            assert error.called and win.vid == 1 and win.sidebar.currentRow() == 1

        win.sidebar.itemClicked.emit(win.sidebar.item(0))
        assert win.vid == 0 and win.playhead == 10
        assert (win.rw, win.rh) == (1, 1)
        assert win.timeline.segments == [(10, 40), (42, 45)]
        assert segments_to_predictions(win.segments, win.rw, win.rh) == original

        timeline = win.timeline
        win.on_seek(2)
        win.btn_mark.click()
        win.on_seek(6)
        win.btn_mark.click()
        assert timeline.segments == [(2, 6), (10, 40), (42, 45)]
        press_undo(win)
        assert win.open_start == 2 and timeline.segments == [(10, 40), (42, 45)]
        press_undo(win)
        assert win.open_start is None

        win.on_seek(43)
        win.btn_add_kf.click()
        assert [k.frame for k in win.segments[1].keyframes] == [42, 43]
        win.btn_del_kf.click()
        assert [k.frame for k in win.segments[1].keyframes] == [42]
        press_undo(win)
        assert [k.frame for k in win.segments[1].keyframes] == [42, 43]
        press_undo(win)
        assert [k.frame for k in win.segments[1].keyframes] == [42]

        _, _, bar_y, _ = timeline._bar()
        drag_timeline(timeline, timeline._x(2), timeline._x(18), bar_y - 6)
        assert win.playhead == 18 and timeline.segments == [(10, 40), (42, 45)]

        win.toggle_play()
        drag_segment(timeline, 0, "start", 1)
        assert not win._playing and win.playhead == 11
        win.on_seek(20)
        press_undo(win)
        assert win.playhead == 10 and timeline.segments == [(10, 40), (42, 45)]
        assert segments_to_predictions(win.segments, win.rw, win.rh) == original
        drag_segment(timeline, 0, "start", 1)
        drag_segment(timeline, 0, "end", -4)
        assert win.playhead == 36 and timeline.segments == [(11, 36), (42, 45)]
        retained = [p for p in original if 11 <= p["frame"] <= 36]
        assert segments_to_predictions(win.segments[:1], win.rw, win.rh) == retained

        drag_segment(timeline, 0, "end", 14)
        assert win.playhead == 41 and timeline.segments == [(11, 41), (42, 45)]
        extended = segments_to_predictions(win.segments[:1], win.rw, win.rh)
        assert extended == retained + [{"frame": f, "bboxes": retained[-1]["bboxes"]}
                                       for f in range(37, 42)]

        drag_segment(timeline, 0, "move", -20)
        assert timeline.segments == [(0, 30), (42, 45)]
        assert segments_to_predictions(win.segments[:1], win.rw, win.rh) == [
            {**p, "frame": p["frame"] - 11} for p in extended
        ]
        drag_segment(timeline, 1, "move", 16)
        assert timeline.segments == [(0, 30), (56, 59)]
        assert load_saved_annotation(output, 0) == old_row

        win.on_seek(57)
        win.btn_undo.click()
        assert timeline.segments == [(0, 30)]
        press_undo(win)
        assert timeline.segments == [(0, 30), (56, 59)]
        press_undo(win)
        assert timeline.segments == [(0, 30), (42, 45)]
        win.on_seek(43)
        win.btn_undo.click()
        win.on_seek(0)
        before_crop = segments_to_predictions(win.segments, win.rw, win.rh)
        win.video_view.boxChanged.emit(4, 20, 40)
        win.video_view.boxChanged.emit(4, 20, 40)
        press_undo(win)
        assert segments_to_predictions(win.segments, win.rw, win.rh) == before_crop
        win.video_view.boxChanged.emit(4, 20, 40)
        edited = segments_to_predictions(win.segments, win.rw, win.rh)
        assert edited[0]["bboxes"] == [4, 20, 40]
        assert [p["frame"] for p in edited] == list(range(31))

        save_and_wait(win)
        assert win.vid == 1
        press_undo(win)
        assert not win.segments and win.open_start is None
        saved = load_saved_annotation(output, 0)
        assert saved["predictions"] == edited
        assert [json.loads(line) for line in summary.read_text(encoding="utf-8").splitlines()] == [
            saved, other_row,
        ]
        assert (output / "0.mp4").stat().st_size > 0

        win.sidebar.itemClicked.emit(win.sidebar.item(0))
        press_undo(win)
        assert segments_to_predictions(win.segments, win.rw, win.rh) == edited
        print("LOAD_DRAG_UNDO_RESAVE_OK")
    finally:
        if win.reader:
            win.reader.release()
        win.close()


if __name__ == "__main__":
    app = QApplication([])
    tmp_root = Path(__file__).resolve().parent / "_tmp"
    tmp_root.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=tmp_root) as tmp:
        check_edit_saved_annotations(Path(tmp))
