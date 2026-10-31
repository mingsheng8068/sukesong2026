"""准备 PdM 公开数据：Azure Predictive Maintenance 五张表，只保留 1–100 号机器。

来源：Microsoft/SQL-Server-R-Services-Samples · PredictiveMaintenanceModelingGuide/Data（CC BY 4.0）
原始数据有 1000 台机器，telemetry 约 800MB；telemetry 按 machineID 排序，读到 101 号就停，
只需下载约前 10%。输出 gzip CSV 到 data/pdm/，可以直接进仓库。

用法：
    python scripts/prepare_pdm_data.py                         # 从网上下载
    python scripts/prepare_pdm_data.py --from data/raw/azure_pdm  # 用已下载的原始 CSV
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import urllib.request
from pathlib import Path
from typing import Iterator

BASE_URL = (
    "https://media.githubusercontent.com/media/Microsoft/SQL-Server-R-Services-Samples/"
    "master/PredictiveMaintenanceModelingGuide/Data/"
)
TABLES = ["machines", "errors", "failures", "maint", "telemetry"]
MAX_MACHINE_ID = 100
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "pdm"


def _open_lines(table: str, source: Path | None) -> Iterator[str]:
    if source is not None:
        with open(source / f"{table}.csv", encoding="utf-8", newline="") as fh:
            yield from fh
        return
    with urllib.request.urlopen(BASE_URL + f"{table}.csv", timeout=60) as response:
        yield from io.TextIOWrapper(response, encoding="utf-8", newline="")


def prepare(table: str, source: Path | None) -> tuple[Path, int]:
    reader = csv.reader(_open_lines(table, source))
    header = next(reader)
    id_col = header.index("machineID")
    out_path = OUT_DIR / f"{table}.csv.gz"
    rows = 0
    # mtime=0：同样的数据每次生成同样的文件，哈希可以拿来核对
    with open(out_path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(header)
        for row in reader:
            machine_id = int(row[id_col])
            if machine_id > MAX_MACHINE_ID:
                if table == "telemetry":
                    break  # telemetry 按机器排序，后面不用再读
                continue
            writer.writerow(row)
            rows += 1
    return out_path, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", type=Path, help="已下载的原始 CSV 目录")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        path, rows = prepare(table, args.source)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"{table:10s} {rows:>8d} 行  {path.stat().st_size / 1e6:6.1f} MB  sha256:{digest}  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
