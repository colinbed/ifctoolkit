"""Small, memory bounded primitives used by IFC -> XLSX extraction."""
from __future__ import annotations

import logging
import os
import resource
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def rss_mb() -> Optional[float]:
    if psutil:
        return round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 2)
    return None


def peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes.
    return round(value / (1048576 if value > 10**8 else 1024), 2)


class ExtractionProfiler:
    """Emit stable, machine-parseable stage telemetry."""

    def __init__(self, logger: logging.Logger, entity_count: int = 0) -> None:
        self.logger = logger
        self.entity_count = entity_count
        self.timings: Dict[str, float] = {}
        self.rows: Dict[str, int] = {}

    @contextmanager
    def stage(self, name: str, *, entities_processed: Optional[int] = None) -> Iterator[Dict[str, int]]:
        started = time.perf_counter()
        stats = {"rows_written": 0}
        try:
            yield stats
        finally:
            elapsed = time.perf_counter() - started
            self.timings[name] = round(elapsed * 1000, 2)
            self.rows[name] = stats["rows_written"]
            self.logger.info(
                "EXCEL_EXTRACTION_STAGE stage=%s elapsed_s=%.3f rss_mb=%s peak_rss_mb=%s "
                "entity_count=%s entities_processed=%s rows_written=%s",
                name, elapsed, rss_mb(), peak_rss_mb(), self.entity_count,
                self.entity_count if entities_processed is None else entities_processed,
                stats["rows_written"],
            )


def append_header(ws: Any, columns: list[str]) -> None:
    """Style only the header; per-cell body styles explode openpyxl memory."""
    row = []
    for value in columns:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        row.append(cell)
    ws.append(row)

