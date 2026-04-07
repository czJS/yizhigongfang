"""
Backend-facing wrapper for quality report logic.

The rule-based implementation lives in `pipelines/lib/quality/quality_report.py` so it can be reused
outside the API layer (e.g. evaluation tooling) and keeps the backend package lighter.
"""

from pipelines.lib.quality.quality_report import SrtItem, generate_quality_report, parse_srt, write_quality_report


