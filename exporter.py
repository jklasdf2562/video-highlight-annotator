# -*- coding: utf-8 -*-
"""导出：官方格式 json + 汇总 jsonl + 打标对比视频。"""
import os
import json
import subprocess
import tempfile

import numpy as np
import cv2

from model import interp_box, box_height


def build_line(video_id, rw, rh, predictions):
    return {
        "video_id": str(video_id),
        "targetRatioWH": [int(rw), int(rh)],
        "predictions": predictions,
    }


def _write_text(path, text):
    """写入完成后替换旧文件，避免半写入的结果覆盖已有标注。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_video_json(video_id, rw, rh, predictions, out_dir):
    line = build_line(video_id, rw, rh, predictions)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"{video_id}.json")
    _write_text(p, json.dumps(line, ensure_ascii=False, indent=2))
    return p


def update_jsonl(video_id, rw, rh, predictions, out_dir):
    """替换当前视频的汇总记录，并清除该视频旧的重复记录。"""
    line = json.dumps(build_line(video_id, rw, rh, predictions), ensure_ascii=False)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "annotations.jsonl")
    try:
        with open(p, encoding="utf-8-sig") as f:
            old_lines = f.readlines()
    except FileNotFoundError:
        old_lines = []
    lines = []
    replaced = False
    for old_line in old_lines:
        try:
            entry = json.loads(old_line)
        except json.JSONDecodeError:
            entry = None
        if isinstance(entry, dict) and str(entry.get("video_id")) == str(video_id):
            if not replaced:
                lines.append(line + "\n")
                replaced = True
        else:
            lines.append(old_line.rstrip("\n") + "\n")
    if not replaced:
        lines.append(line + "\n")
    _write_text(p, "".join(lines))
    return p


def _timeline(canvas, x, y, w, h, segments, total, cur, fps):
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (28, 30, 34), -1)
    pad = 12
    bar_x = x + pad
    bar_w = w - 2 * pad
    bar_y = y + 34
    bar_h = max(8, h - 34 - 22)
    cv2.rectangle(canvas, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (60, 62, 66), -1)
    if total > 0:
        for s, e in segments:
            sx = bar_x + int(s / total * bar_w)
            ex = bar_x + int(min(e, total) / total * bar_w)
            ex = max(ex, sx + 1)
            cv2.rectangle(canvas, (sx, bar_y), (ex, bar_y + bar_h), (255, 138, 0), -1)
        px = bar_x + int(min(cur, total) / total * bar_w)
        cv2.line(canvas, (px, bar_y - 3), (px, bar_y + bar_h + 3), (255, 255, 255), 2)
    # 时间刻度
    dur = total / fps if fps else 0
    t = 0.0
    while t <= dur + 1e-6:
        xx = bar_x + int((t / dur) * bar_w) if dur > 0 else bar_x
        cv2.line(canvas, (xx, bar_y + bar_h), (xx, bar_y + bar_h + 4), (150, 154, 158), 1)
        mm, ss = int(t // 60), int(t % 60)
        cv2.putText(canvas, f"{mm}:{ss:02d}", (xx - 10, bar_y + bar_h + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 204, 208), 1, cv2.LINE_AA)
        t += 10.0


def render_comparison(video_path, segments, rw, rh, out_mp4, progress_cb=None):
    """生成打标对比视频：左=原片+裁剪框，右=裁剪成片，下=时间轴。原生帧率。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 布局
    DISP_H = 540
    PAD, GAP, TL_GAP, TL_H, BOTTOM = 16, 16, 16, 88, 16
    TITLE_H = 0
    scale = DISP_H / H
    L_W = int(round(W * scale))
    L_H = DISP_H
    R_W = int(round(DISP_H * rw / rh))
    R_H = DISP_H
    lx, ly = PAD, PAD
    rx, ry = PAD + L_W + GAP, PAD
    tx, ty = PAD, PAD + DISP_H + TL_GAP
    tw = L_W + GAP + R_W
    CW = PAD * 2 + L_W + GAP + R_W
    CH = PAD + DISP_H + TL_GAP + TL_H + BOTTOM
    CW -= CW % 2
    CH -= CH % 2

    # 预建每个高光帧的框
    seg_map = {}
    for seg in segments:
        kfs = seg.sorted_kf()
        for f in range(seg.start, seg.end + 1):
            box = interp_box(kfs, f)
            if box is not None:
                seg_map[f] = (seg, box)

    use_ffmpeg = True
    try:
        pipe = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{CW}x{CH}", "-r", f"{fps:.4f}",
             "-i", "-", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", out_mp4],
            stdin=subprocess.PIPE)
    except FileNotFoundError:
        use_ffmpeg = False
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        vw = cv2.VideoWriter(out_mp4, fourcc, fps, (CW, CH))
        if not vw.isOpened():
            cap.release()
            raise RuntimeError("无法写入对比视频")

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        canvas = np.full((CH, CW, 3), 22, dtype=np.uint8)

        # 左：原片 letterbox
        left = np.zeros((L_H, L_W, 3), dtype=np.uint8)
        rsz = cv2.resize(frame, (L_W, L_H), interpolation=cv2.INTER_AREA)
        left[:, :] = rsz
        if idx in seg_map:
            _, (x, y, w) = seg_map[idx]
            h = box_height(w, rw, rh)
            X1 = int(round(x * scale)); Y1 = int(round(y * scale))
            X2 = int(round((x + w) * scale)); Y2 = int(round((y + h) * scale))
            X1 = max(0, min(X1, L_W)); X2 = max(0, min(X2, L_W))
            Y1 = max(0, min(Y1, L_H)); Y2 = max(0, min(Y2, L_H))
            orig = left.copy()
            left = cv2.addWeighted(left, 0.42, np.zeros_like(left), 0.58, 0)
            if Y2 > Y1 and X2 > X1:
                left[Y1:Y2, X1:X2] = orig[Y1:Y2, X1:X2]
            cv2.rectangle(left, (X1, Y1), (X2, Y2), (0, 200, 80), 2)
        canvas[ly:ly + L_H, lx:lx + L_W] = left

        # 右：裁剪成片 / 非高光占位
        if idx in seg_map:
            _, (x, y, w) = seg_map[idx]
            h = box_height(w, rw, rh)
            x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
            x2 = min(W, x + w); y2 = min(H, y + h)
            crop = frame[y:y2, x:x2] if (x2 > x and y2 > y) else frame
            right = cv2.resize(crop, (R_W, R_H), interpolation=cv2.INTER_LINEAR)
            cv2.rectangle(right, (0, 0), (R_W - 1, R_H - 1), (0, 200, 80), 2)
        else:
            right = np.full((R_H, R_W, 3), 15, dtype=np.uint8)
        canvas[ry:ry + R_H, rx:rx + R_W] = right

        # 时间轴
        seg_ranges = [(s.start, s.end) for s in segments]
        _timeline(canvas, tx, ty, tw, TL_H, seg_ranges, total, idx, fps)

        if use_ffmpeg:
            pipe.stdin.write(canvas.tobytes())
        else:
            vw.write(canvas)
        idx += 1
        if progress_cb and idx % 30 == 0:
            progress_cb(idx, total)

    if use_ffmpeg:
        pipe.stdin.close()
        status = pipe.wait()
        if status != 0:
            cap.release()
            raise RuntimeError(f"ffmpeg 退出码：{status}")
    else:
        vw.release()
    cap.release()
    return idx
