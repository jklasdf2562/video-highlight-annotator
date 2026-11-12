# -*- coding: utf-8 -*-
"""配置：源视频目录、官方比例文件夹(必填)、预填结果(可选)、输出目录。"""
import os
import re
import json
from dataclasses import dataclass, field, asdict

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts"}


@dataclass
class Config:
    source_dir: str = ""      # 源视频文件夹
    ratio_dir: str = ""       # 必填：官方比例文件夹（{id}.jsonl，每行 {"targetRatioWH":[w,h]}）
    prefill_jsonl: str = ""   # 可选：预填结果 jsonl（空 = 不预填，自动初始化）
    out_dir: str = ""         # 输出文件夹（json + 对比视频 + annotations.jsonl）
    default_ratio: list = field(default_factory=lambda: [16, 9])

    def save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load():
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    d = json.load(f)
                fields = Config.__dataclass_fields__
                return Config(**{k: d.get(k, v) for k, v in
                                 {k: None for k in fields}.items()})
            except Exception:
                pass
        return Config()


def scan_videos(source_dir):
    """返回 [(video_id, filename, fullpath), ...]，按 video_id 升序。"""
    items = []
    if not source_dir or not os.path.isdir(source_dir):
        return items
    for fn in sorted(os.listdir(source_dir)):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in VIDEO_EXTS:
            continue
        m = re.match(r"^(\d+)$", base)
        vid = int(m.group(1)) if m else None
        if vid is None:
            m2 = re.match(r"^[^\d]*(\d+)", base)
            vid = int(m2.group(1)) if m2 else None
        items.append((vid if vid is not None else -1, fn, os.path.join(source_dir, fn)))
    items.sort(key=lambda t: (t[0] < 0, t[0]))
    return items


def load_ratio(ratio_dir, vid):
    """从官方比例文件夹读该视频的 targetRatioWH；找不到返回 None。"""
    if ratio_dir and vid is not None:
        p = os.path.join(ratio_dir, f"{vid}.jsonl")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            o = json.loads(line)
                            rw, rh = o["targetRatioWH"]
                            return [int(rw), int(rh)]
            except Exception:
                pass
    return None


def load_prefill(prefill_jsonl):
    """解析预填结果 jsonl -> {video_id(int): {'targetRatioWH':[..], 'predictions':[..]}}"""
    data = {}
    if not prefill_jsonl or not os.path.exists(prefill_jsonl):
        return data
    with open(prefill_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                data[int(o["video_id"])] = o
            except Exception:
                continue
    return data


def load_saved_annotation(out_dir, vid):
    """读取工具导出的单视频标注；文件不存在时返回 None。"""
    path = os.path.join(out_dir, f"{vid}.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if str(data["video_id"]) != str(vid):
            raise ValueError("video_id 与当前视频编号不匹配")
        rw, rh = data["targetRatioWH"]
        if type(rw) is not int or type(rh) is not int or min(rw, rh) <= 0:
            raise ValueError("targetRatioWH 必须是两个正整数")
        predictions = data["predictions"]
        if not isinstance(predictions, list):
            raise ValueError("predictions 必须是列表")
        for pred in predictions:
            frame = pred["frame"]
            x, y, w = pred["bboxes"]
            if (type(frame) is not int or frame < 0
                    or any(type(v) is not int for v in (x, y, w))
                    or min(x, y) < 0 or w <= 0):
                raise ValueError("predictions 中的 frame 或 bboxes 无效")
    except FileNotFoundError:
        return None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}：{exc}") from exc
    return data
