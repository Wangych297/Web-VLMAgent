from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from browser.actions import CandidateElement, parse_candidates
from perception.element_filter import filter_markable_candidates


MARK_COLORS = [
    (230, 57, 70),
    (29, 53, 87),
    (42, 157, 143),
    (245, 130, 32),
    (106, 76, 147),
    (0, 109, 119),
]


def load_candidates(path: Path) -> list[CandidateElement]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return parse_candidates(data)


def draw_set_of_marks(
    screenshot_path: Path,
    candidates: Iterable[CandidateElement | dict[str, Any]],
    output_path: Path,
    min_area: float = 64.0,
    min_size: float = 4.0,
) -> list[CandidateElement]:
    image = Image.open(screenshot_path).convert("RGB")
    width, height = image.size
    markable_candidates = filter_markable_candidates(
        candidates,
        image_width=width,
        image_height=height,
        min_area=min_area,
        min_size=min_size,
    )

    draw = ImageDraw.Draw(image)
    font = _load_font()

    for candidate in markable_candidates:
        color = MARK_COLORS[(candidate.element_id - 1) % len(MARK_COLORS)]
        rect = _clamp_rect(
            candidate.bbox.x,
            candidate.bbox.y,
            candidate.bbox.x + candidate.bbox.width,
            candidate.bbox.y + candidate.bbox.height,
            width,
            height,
        )
        draw.rectangle(rect, outline=color, width=3)
        _draw_label(draw, font, str(candidate.element_id), rect, color, width, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return markable_candidates


def draw_set_of_marks_from_files(
    screenshot_path: Path,
    candidates_path: Path,
    output_path: Path,
    min_area: float = 64.0,
    min_size: float = 4.0,
) -> list[CandidateElement]:
    candidates = load_candidates(candidates_path)
    return draw_set_of_marks(
        screenshot_path=screenshot_path,
        candidates=candidates,
        output_path=output_path,
        min_area=min_area,
        min_size=min_size,
    )


def _load_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", 14)
    except OSError:
        return ImageFont.load_default()


def _clamp_rect(
    left: float,
    top: float,
    right: float,
    bottom: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    x1 = max(0, min(image_width - 1, int(round(left))))
    y1 = max(0, min(image_height - 1, int(round(top))))
    x2 = max(0, min(image_width - 1, int(round(right))))
    y2 = max(0, min(image_height - 1, int(round(bottom))))
    return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    label: str,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    image_width: int,
    image_height: int,
) -> None:
    x1, y1, _, _ = rect
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    padding_x = 5
    padding_y = 3
    label_width = text_width + padding_x * 2
    label_height = text_height + padding_y * 2

    label_x1 = min(max(0, x1), max(0, image_width - label_width))
    label_y1 = min(max(0, y1), max(0, image_height - label_height))
    label_rect = (
        label_x1,
        label_y1,
        label_x1 + label_width,
        label_y1 + label_height,
    )

    draw.rounded_rectangle(label_rect, radius=3, fill=color)
    draw.text(
        (label_x1 + padding_x, label_y1 + padding_y - 1),
        label,
        fill=(255, 255, 255),
        font=font,
    )
