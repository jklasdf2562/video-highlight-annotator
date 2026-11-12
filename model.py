# -*- coding: utf-8 -*-
"""数据模型：高光段落、裁剪关键帧、线性插值、与官方格式互转。"""
from dataclasses import dataclass, field


@dataclass
class Keyframe:
    frame: int   # 帧号
    x: int       # 裁剪框左上角 x
    y: int       # 裁剪框左上角 y
    w: int       # 裁剪框宽度（高度 = w * rh / rw）


@dataclass
class Segment:
    start: int
    end: int
    keyframes: list = field(default_factory=list)  # list[Keyframe]

    def sorted_kf(self):
        return sorted(self.keyframes, key=lambda k: k.frame)


def interp_box(kfs, frame):
    """在关键帧序列中按帧号线性插值得到 (x, y, w)；首帧前保持首个、末帧后保持末个。"""
    if not kfs:
        return None
    kfs = sorted(kfs, key=lambda k: k.frame)
    if frame <= kfs[0].frame:
        k = kfs[0]
        return (k.x, k.y, k.w)
    if frame >= kfs[-1].frame:
        k = kfs[-1]
        return (k.x, k.y, k.w)
    for a, b in zip(kfs, kfs[1:]):
        if a.frame <= frame <= b.frame:
            if b.frame == a.frame:
                return (b.x, b.y, b.w)
            t = (frame - a.frame) / (b.frame - a.frame)
            x = round(a.x + (b.x - a.x) * t)
            y = round(a.y + (b.y - a.y) * t)
            w = round(a.w + (b.w - a.w) * t)
            return (x, y, w)
    k = kfs[-1]
    return (k.x, k.y, k.w)


def box_height(w, rw, rh):
    return max(1, int(round(w * rh / rw)))


def default_keyframe(frame, orig_w, orig_h, rw, rh):
    """默认裁剪框：能放入画面的、比例锁定的最大居中框。"""
    if orig_w / max(orig_h, 1) >= rw / rh:
        # 源比目标更宽 -> 高度填满，宽度向下取整保证派生高度不越界
        h = orig_h
        w = int(h * rw / rh)
    else:
        w = orig_w
        h = int(w * rh / rw)
    w = max(1, min(w, orig_w))
    h = max(1, min(h, orig_h))
    x = (orig_w - w) // 2
    y = (orig_h - h) // 2
    return Keyframe(frame, x, y, w)


def clamp_box(x, y, w, h, W, H):
    """把框钳制在画面内，并保持 w,h 不越界。"""
    w = max(1, min(w, W))
    h = max(1, min(h, H))
    x = max(0, min(x, W - w))
    y = max(0, min(y, H - h))
    return x, y, w, h


def group_frames(frames, gap=2):
    """把离散帧号按连续(间隔<=gap)分组 -> [(start, end), ...] 闭区间。"""
    if not frames:
        return []
    frames = sorted(set(frames))
    groups = []
    s = prev = frames[0]
    for f in frames[1:]:
        if f - prev <= gap:
            prev = f
        else:
            groups.append((s, prev))
            s = prev = f
    groups.append((s, prev))
    return groups


def simplify_keyframes(preds, max_kf=20):
    """把逐帧 predictions 抽稀成 <=max_kf 个关键帧（保留首尾 + 均匀采样）。"""
    preds = sorted(preds, key=lambda p: p["frame"])
    n = len(preds)
    if n == 0:
        return []
    if n <= max_kf:
        idxs = list(range(n))
    else:
        step = (n - 1) / (max_kf - 1)
        idxs = sorted(set([0, n - 1] + [int(round(i * step)) for i in range(1, max_kf)]))
    out = []
    for i in idxs:
        b = preds[i]["bboxes"]
        out.append(Keyframe(preds[i]["frame"], int(b[0]), int(b[1]), int(b[2])))
    return out


def predictions_to_model(preds, gap=2, max_kf=20):
    """把官方 predictions -> [Segment...]（每段带抽稀后的关键帧）。"""
    segs = []
    for s, e in group_frames([p["frame"] for p in preds], gap):
        seg_preds = [p for p in preds if s <= p["frame"] <= e]
        kfs = simplify_keyframes(seg_preds, max_kf)
        if not kfs:
            continue
        segs.append(Segment(s, e, kfs))
    return segs


def _restore_segment(boxes):
    """仅移除可由现有插值规则精确重建的逐帧裁剪框。"""
    keyframes = [boxes[0]]
    spans = [(0, len(boxes) - 1)]
    while spans:
        start, end = spans.pop()
        if start == end:
            continue
        endpoints = (boxes[start], boxes[end])
        if all(interp_box(endpoints, k.frame) == (k.x, k.y, k.w)
               for k in boxes[start + 1:end]):
            keyframes.append(boxes[end])
        else:
            mid = (start + end) // 2
            spans.extend(((mid, end), (start, mid)))

    # 末关键帧之后本来就保持位置，重复的末帧可以省去。
    if len(keyframes) > 1:
        prev, last = keyframes[-2:]
        if (prev.x, prev.y, prev.w) == (last.x, last.y, last.w):
            keyframes.pop()
    return Segment(boxes[0].frame, boxes[-1].frame, keyframes)


def move_segment(segment, start):
    """整体平移，保持片段长度和裁剪框相对于片段的位置。"""
    delta = start - segment.start
    keyframes = [Keyframe(k.frame + delta, k.x, k.y, k.w) for k in segment.keyframes]
    return Segment(start, segment.end + delta, keyframes)


def resize_segment(segment, start, end):
    """调整闭区间，保留覆盖帧的裁剪框，新增帧沿用端点框。"""
    original = segment.sorted_kf()
    retained = [k for k in original if start <= k.frame <= end]
    if len(retained) == len(original):
        return Segment(start, end, retained)

    def restore_span(first, last):
        boxes = [Keyframe(f, *interp_box(original, f)) for f in range(first, last + 1)]
        return _restore_segment(boxes).keyframes

    # 裁断插值区间时，直接用新边界插值可能因取整改变剩余帧的框。
    # 仅重建被裁断的插值区间，保留片段内部的关键帧。
    if not retained:
        retained = restore_span(start, end)
    else:
        first, last = retained[0].frame, retained[-1].frame
        if original[0].frame < start < first:
            retained = [k for k in restore_span(start, first) if k.frame < first] + retained
        if last < end < original[-1].frame:
            retained.extend(restore_span(last, end)[1:])
    return Segment(start, end, retained)


def saved_predictions_to_model(preds):
    """按导出的帧顺序恢复高光段，保留缺帧和重叠段的记录。"""
    segments = []
    boxes = []
    for pred in preds:
        frame = pred["frame"]
        if boxes and frame != boxes[-1].frame + 1:
            segments.append(_restore_segment(boxes))
            boxes = []
        boxes.append(Keyframe(frame, *pred["bboxes"]))
    if boxes:
        segments.append(_restore_segment(boxes))
    return segments


def segments_to_predictions(segments, rw, rh):
    """把 [Segment...] 展开为官方格式 predictions（每帧一个 bbox）。"""
    preds = []
    for seg in segments:
        kfs = seg.sorted_kf()
        for f in range(seg.start, seg.end + 1):
            box = interp_box(kfs, f)
            if box is None:
                continue
            preds.append({"frame": f, "bboxes": [box[0], box[1], box[2]]})
    return preds
