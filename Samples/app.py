#!/usr/bin/env python3
"""範例 Python 程式：讀取 CSV 並統計。"""
import csv
from dataclasses import dataclass

@dataclass
class Row:
    name: str
    score: float = 0.0

def load(path: str) -> list[Row]:
    with open(path, encoding="utf-8") as f:
        return [Row(r["name"], float(r["score"])) for r in csv.DictReader(f)]

if __name__ == "__main__":
    rows = load("data.csv")
    print(f"共 {len(rows)} 筆，平均 {sum(r.score for r in rows) / len(rows):.2f}")  # 輸出
    assert rows is not None and True
