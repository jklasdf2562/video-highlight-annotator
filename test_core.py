# -*- coding: utf-8 -*-
"""无 GUI 核心逻辑自测（仅 stdlib，可用系统 python 运行）。"""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from model import (
    Keyframe, Segment, interp_box, box_height, default_keyframe,
    clamp_box, resize_segment, predictions_to_model, saved_predictions_to_model, segments_to_predictions,
)
from config import load_ratio, load_prefill, load_saved_annotation

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        ok = False


# 1. 线性插值 + 保持
kfs = [Keyframe(0, 0, 0, 100), Keyframe(100, 100, 50, 200)]
check("interp before", interp_box(kfs, -5) == (0, 0, 100))
check("interp mid", interp_box(kfs, 50) == (50, 25, 150))
check("interp after", interp_box(kfs, 300) == (100, 50, 200))
check("interp single", interp_box([Keyframe(10, 5, 6, 7)], 99) == (5, 6, 7))

# 2. box_height
check("height 16:9", box_height(720, 16, 9) == 405)
check("height 9:16", box_height(607, 9, 16) == 1079)

# 3. default_keyframe
k = default_keyframe(0, 720, 1280, 16, 9)   # 竖屏 -> 全宽
check("default portrait 16:9", (k.x, k.y, k.w) == (0, 437, 720), f"got {k}")
k = default_keyframe(0, 1920, 1080, 9, 16)  # 横屏 -> 全高
check("default landscape 9:16", (k.x, k.y, k.w) == (656, 0, 607), f"got {k}")

# 4. 分组与往返
preds = [{"frame": 90, "bboxes": [0, 393, 720]}, {"frame": 91, "bboxes": [0, 392, 720]},
         {"frame": 95, "bboxes": [10, 300, 720]}]   # 91->95 间隔 4 > gap=2，应拆成两段
segs = predictions_to_model(preds, gap=2, max_kf=20)
check("gap split -> 2 segments", len(segs) == 2, f"got {len(segs)}")
check("seg[0] range", (segs[0].start, segs[0].end) == (90, 91))
check("seg[1] range", (segs[1].start, segs[1].end) == (95, 95))

preds2 = [{"frame": 90, "bboxes": [0, 393, 720]}, {"frame": 91, "bboxes": [0, 392, 720]},
          {"frame": 92, "bboxes": [0, 391, 720]}]
segs2 = predictions_to_model(preds2, gap=2, max_kf=20)
check("consecutive -> 1 segment", len(segs2) == 1, f"got {len(segs2)}")
back = segments_to_predictions(segs2, 16, 9)
check("roundtrip frame count", len(back) == 3, f"got {len(back)}")
check("roundtrip bbox exact",
      back[0]["bboxes"] == [0, 393, 720] and back[2]["bboxes"] == [0, 391, 720])

# 5. clamp
check("clamp", clamp_box(-5, -5, 100, 100, 720, 1280) == (0, 0, 100, 100))

# 6. 逐帧还原保存结果，保留单帧间隙和重叠段
saved_preds = [{"frame": f, "bboxes": [f % 2, 8, 40]} for f in range(1, 26)]
saved_preds += [{"frame": f, "bboxes": [2, 4, 48]} for f in (27, 28, 28)]
restored = saved_predictions_to_model(saved_preds)
check("saved segment ranges", [(s.start, s.end) for s in restored] == [(1, 25), (27, 28), (28, 28)])
check("saved bbox roundtrip", segments_to_predictions(restored, 9, 16) == saved_preds)
check("fixed box has one keyframe", len(restored[1].keyframes) == 1)

# 裁掉插值端点时仍精确保留逐帧框，包括只剩一帧的片段。
clip = Segment(0, 10, [Keyframe(0, 0, 0, 20), Keyframe(5, 2, 0, 20), Keyframe(10, 0, 0, 20)])
clip_preds = segments_to_predictions([clip], 16, 9)
trimmed = resize_segment(clip, 1, 9)
check("trim preserves rounded crops", segments_to_predictions([trimmed], 16, 9) == clip_preds[1:10])
check("trim keeps interior keyframe", clip.keyframes[1] in trimmed.keyframes)
check("trim to one frame", segments_to_predictions([resize_segment(clip, 4, 4)], 16, 9) == clip_preds[4:5])

# 7. 官方比例、预填和保存结果使用临时文件自测
tmp_root = Path(__file__).resolve().parent / "_tmp"
tmp_root.mkdir(exist_ok=True)
with TemporaryDirectory(dir=tmp_root) as tmp:
    folder = Path(tmp)
    row = {"video_id": "0", "targetRatioWH": [9, 16], "predictions": saved_preds}
    (folder / "0.jsonl").write_text(json.dumps({"targetRatioWH": [16, 9]}) + "\n", encoding="utf-8")
    prefill = folder / "prefill.jsonl"
    prefill.write_text(json.dumps(row) + "\n", encoding="utf-8")
    check("official ratio", load_ratio(folder, 0) == [16, 9])
    check("prefill loaded", load_prefill(prefill) == {0: row})
    check("missing output json", load_saved_annotation(folder, 0) is None)
    saved_file = folder / "0.json"
    saved_file.write_text(json.dumps(row), encoding="utf-8-sig")
    check("output json loaded", load_saved_annotation(folder, 0) == row)
    saved_file.write_text(json.dumps({**row, "video_id": "1"}), encoding="utf-8")
    try:
        load_saved_annotation(folder, 0)
    except ValueError:
        check("reject mismatched output id", True)
    else:
        check("reject mismatched output id", False)

print("\nRESULT:", "ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
