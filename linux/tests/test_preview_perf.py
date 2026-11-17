"""預覽核心：效能（2 MiB 原始碼要在 1.5 秒內上色完成）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from quickseemd.core.preview import highlight, languages

BUDGET_SECONDS = 1.5
TARGET_BYTES = 2 * 1024 * 1024

PY_CHUNK = '''
@dataclass(frozen=True)
class Point(Base):
    """二維座標。"""
    x: float = 0.0
    y: float = 1.5e-3

    def distance(self, other: "Point") -> float:
        # 歐幾里得距離
        dx, dy = self.x - other.x, self.y - other.y
        return math.sqrt(dx * dx + dy * dy) if dx or dy else 0x0


async def main(argv: list[str]) -> int:
    for i, arg in enumerate(sorted(argv)):
        print(f"{i}: {arg!r}", 'single', None, True)
    return len(argv) // 2
'''

SWIFT_CHUNK = """
/// 文件註解
struct Counter<T: Hashable>: Sendable {
    private var counts: [T: Int] = [:]
    mutating func add(_ value: T, times: Int = 1) {
        counts[value, default: 0] += times // 累加
    }
    func top(_ n: Int) -> [(T, Int)] { counts.sorted { $0.value > $1.value }.prefix(n).map { ($0.key, $0.value) } }
}
#if DEBUG
let greeting = "你好, \\(name)!" ; let hex = 0xFF_FF
#endif
"""

JSON_CHUNK = '{"id": 12345, "name": "測試項目", "tags": ["a", "b"], "ok": true, "v": null},\n'


def _repeat(chunk: str) -> str:
    raw = chunk.encode()
    return (raw * (TARGET_BYTES // len(raw) + 1))[:TARGET_BYTES].decode("utf-8", "ignore")


@pytest.mark.parametrize(
    ("lang", "chunk"),
    [("python", PY_CHUNK), ("swift", SWIFT_CHUNK), ("json", JSON_CHUNK)],
    ids=["python", "swift", "json"],
)
def test_two_mib_highlight_within_budget(lang: str, chunk: str) -> None:
    text = _repeat(chunk)
    start = time.perf_counter()
    html, lines = highlight.code_view(text, languages.BY_ID[lang])
    elapsed = time.perf_counter() - start
    assert lines > 10_000
    assert '<span class="k">' in html or '<span class="y">' in html
    assert elapsed < BUDGET_SECONDS, f"{lang}: {elapsed:.2f}s"


def test_two_mib_file_render_within_budget(tmp_path: Path) -> None:
    from quickseemd.core.preview import engine

    f = tmp_path / "big.py"
    f.write_text(_repeat(PY_CHUNK), "utf-8")
    start = time.perf_counter()
    doc = engine.render(f)
    elapsed = time.perf_counter() - start
    assert doc.kind_label == "Python"
    assert elapsed < BUDGET_SECONDS, f"render: {elapsed:.2f}s"
