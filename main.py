# -*- coding: utf-8 -*-
"""入口：配置对话框 -> 主窗口。"""
import os
import sys

from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QPushButton, QFileDialog,
    QGridLayout, QHBoxLayout, QVBoxLayout, QMessageBox,
)

from config import Config, scan_videos, load_prefill
from main_window import MainWindow


class SetupDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("打标设置")
        self.cfg = cfg

        self.src = QLineEdit(cfg.source_dir)
        self.ratio = QLineEdit(cfg.ratio_dir)
        self.prefill = QLineEdit(cfg.prefill_jsonl)
        self.compare = QLineEdit(cfg.compare_jsonl)
        self.out = QLineEdit(cfg.out_dir)

        grid = QGridLayout()
        grid.addWidget(QLabel("源视频文件夹："), 0, 0)
        grid.addWidget(self.src, 0, 1)
        grid.addWidget(self._browse_dir(self.src), 0, 2)

        grid.addWidget(QLabel("官方比例文件夹(必填)："), 1, 0)
        grid.addWidget(self.ratio, 1, 1)
        grid.addWidget(self._browse_dir(self.ratio), 1, 2)

        grid.addWidget(QLabel("预填结果 jsonl(可选)："), 2, 0)
        grid.addWidget(self.prefill, 2, 1)
        grid.addWidget(self._browse_file(self.prefill), 2, 2)

        grid.addWidget(QLabel("对比预设 jsonl(可选)："), 3, 0)
        grid.addWidget(self.compare, 3, 1)
        grid.addWidget(self._browse_file(self.compare), 3, 2)

        grid.addWidget(QLabel("输出文件夹："), 4, 0)
        grid.addWidget(self.out, 4, 1)
        grid.addWidget(self._browse_dir(self.out), 4, 2)

        btns = QHBoxLayout()
        ok = QPushButton("开始")
        cancel = QPushButton("退出")
        ok.setDefault(True)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)

        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addLayout(btns)
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)

    def _browse_dir(self, edit):
        b = QPushButton("浏览…")
        b.clicked.connect(lambda: self._pick_dir(edit))
        return b

    def _browse_file(self, edit):
        b = QPushButton("浏览…")
        b.clicked.connect(lambda: self._pick_file(edit))
        return b

    def _pick_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹", edit.text() or "")
        if d:
            edit.setText(d)

    def _pick_file(self, edit):
        f, _ = QFileDialog.getOpenFileName(self, "选择 jsonl", edit.text() or "",
                                           "JSONL (*.jsonl);;所有文件 (*)")
        if f:
            edit.setText(f)

    def _ok(self):
        src = self.src.text().strip()
        ratio = self.ratio.text().strip()
        out = self.out.text().strip()
        if not src:
            QMessageBox.warning(self, "提示", "请选择源视频文件夹。")
            return
        if not ratio:
            QMessageBox.warning(self, "提示", "请选择官方比例文件夹（必填）。")
            return
        if not out:
            QMessageBox.warning(self, "提示", "请选择输出文件夹。")
            return
        self.cfg.source_dir = src
        self.cfg.ratio_dir = ratio
        self.cfg.prefill_jsonl = self.prefill.text().strip()
        self.cfg.compare_jsonl = self.compare.text().strip()
        self.cfg.out_dir = out
        self.cfg.save()
        self.accept()


def main():
    app = QApplication(sys.argv)
    cfg = Config.load()

    if not (cfg.source_dir and cfg.ratio_dir and cfg.out_dir):
        dlg = SetupDialog(cfg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0

    prefill = load_prefill(cfg.prefill_jsonl)
    compare = load_prefill(cfg.compare_jsonl)
    video_list = scan_videos(cfg.source_dir)
    win = MainWindow(cfg, video_list, prefill, compare)

    def on_settings():
        dlg = SetupDialog(cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            prefill = load_prefill(cfg.prefill_jsonl)
            compare = load_prefill(cfg.compare_jsonl)
            video_list = scan_videos(cfg.source_dir)
            win.reload(cfg, video_list, prefill, compare)

    win.settingsRequested.connect(on_settings)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
