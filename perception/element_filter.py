from __future__ import annotations

from typing import Any, Iterable

from browser.actions import CandidateElement, parse_candidates


def is_candidate_markable(
    candidate: CandidateElement,
    image_width: int,
    image_height: int,
    min_area: float = 64.0,
    min_size: float = 4.0,
) -> bool:
    bbox = candidate.bbox

    if not candidate.visible:
        return False

    if bbox.width < min_size or bbox.height < min_size:
        return False

    if bbox.width * bbox.height < min_area:
        return False

    if bbox.x >= image_width or bbox.y >= image_height:
        return False

    if bbox.x + bbox.width <= 0 or bbox.y + bbox.height <= 0:
        return False

    return True


def filter_markable_candidates(
    candidates: Iterable[CandidateElement | dict[str, Any]],
    image_width: int,
    image_height: int,
    min_area: float = 64.0,
    min_size: float = 4.0,
) -> list[CandidateElement]:
    parsed_candidates = parse_candidates(candidates)
    return [
        candidate
        for candidate in parsed_candidates
        if is_candidate_markable(
            candidate,
            image_width=image_width,
            image_height=image_height,
            min_area=min_area,
            min_size=min_size,
        )
    ]
