"""Truy xuất đoạn playbook bằng BM25 (playbook nhỏ, từ khóa nghiệp vụ rõ ràng)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi


@dataclass
class Chunk:
    id: str
    title: str
    text: str
    steps: list[str]


def _tok(s: str) -> list[str]:
    s = unicodedata.normalize("NFC", s.lower())
    return re.findall(r"[\wÀ-ỹ]+", s)


def load_playbook(path: Path) -> list[Chunk]:
    chunks, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(PB-\d+)\s+(.*)$", line.strip())
        if m:
            cur = Chunk(m.group(1), m.group(2).strip(), "", [])
            chunks.append(cur)
        elif cur is not None and line.strip():
            cur.text += line.strip() + "\n"
            if line.strip().startswith("- "):
                cur.steps.append(line.strip()[2:])
    if not chunks:
        raise ValueError(f"Playbook {path} không có đoạn nào dạng '## PB-xx Tiêu đề'")
    return chunks


class Retriever:
    def __init__(self, path: Path):
        self.chunks = load_playbook(path)
        self.bm25 = BM25Okapi([_tok(c.title + " " + c.text) for c in self.chunks])

    def search(self, query: str, k: int) -> list[Chunk]:
        scores = self.bm25.get_scores(_tok(query))
        order = sorted(range(len(self.chunks)), key=lambda i: -scores[i])[:k]
        return [self.chunks[i] for i in order]


def build_query(case: dict, signals: list[str]) -> str:
    """Truy vấn dựng từ hồ sơ: mức phân luồng, loại merchant, các tín hiệu rủi ro."""
    parts = [f"hồ sơ {case.get('route', '')}", str(case.get("category") or "")]
    if case.get("route") == "UNSCORED":
        parts.append("không chấm được điểm dữ liệu lỗi")
    parts += signals
    return " ".join(parts)
