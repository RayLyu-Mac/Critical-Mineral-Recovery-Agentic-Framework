# -*- coding: utf-8 -*-
"""Page helpers for the economic-loop report.

Used when AgentLoopDemo_Pipeline/script/build_chain_html.py cannot be read.
That file is sometimes an OneDrive placeholder, and open() then raises
OSError 22. The page shell is chain_page.html, taken from the last report
that the chain builder produced.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CLIP = 4500
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".htm"}
PAGE = Path(__file__).with_name("chain_page.html").read_text(encoding="utf-8")


def clip(text: str, limit: int = CLIP) -> str:
    text = str(text).replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… truncated ({len(text)} characters)"


def read_clip(path: Path, limit: int = CLIP) -> str:
    if not path.is_file():
        return ""
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(file could not be read)"
    return clip(body, limit)


def text_or(text: str, missing: str = "No text recorded.") -> str:
    text = str(text or "").strip()
    return text or missing


def show_files(paths: list[Path], root: Path) -> str:
    chunks = []
    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if path.suffix.lower() in SKIP_EXT:
            chunks.append(f"=== {rel} ===\n[file omitted: {path.suffix}]")
            continue
        body = read_clip(path) or "(empty file)"
        chunks.append(f"=== {rel} ===\n{body}")
    return "\n\n".join(chunks)


def files_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    try:
        names = list(folder.iterdir())
    except OSError:
        return []
    return sorted(path for path in names if path.is_file())


def fmt_num(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _cell(name: str, value: str, changed: bool) -> dict:
    return {"name": name, "value": value, "changed": changed}


def pane(thinking: str, inp: str, output: str, image: str = "", images: list[str] | None = None) -> dict:
    row = {
        "thinking": text_or(thinking),
        "input": text_or(inp),
        "output": text_or(output),
    }
    pics = [src for src in (images or []) if src]
    if image and image not in pics:
        pics.insert(0, image)
    if pics:
        row["image"] = pics[0]
        row["images"] = pics
    return row


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data


def stage_plot(folder: Path) -> Path | None:
    """PNG for this loop. Prefer the path stored in ph90_95.json."""
    data = _read_json(folder / "05_S01" / "ph90_95.json")
    if isinstance(data, dict):
        for stage in data.get("stages") or []:
            if isinstance(stage, dict) and stage.get("plot"):
                path = Path(str(stage["plot"]))
                if path.is_file():
                    return path
    image_dir = folder / "05_S01" / "image"
    if image_dir.is_dir():
        found = sorted(image_dir.glob("stage*.png"))
        if found:
            return found[0]
    return None


def image_src(page_dir: Path, path: Path | None) -> str:
    """Path of the PNG relative to the HTML file."""
    if path is None or not path.is_file():
        return ""
    return Path(os.path.relpath(path.resolve(), page_dir.resolve())).as_posix()
