"""
Lite pipeline entrypoint.

Keeps symmetry with `quality_pipeline.py` and `online_pipeline.py`.
"""

from __future__ import annotations


def main() -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from pipelines.lite_pipeline_impl import main as impl_main  # type: ignore

    impl_main()


if __name__ == "__main__":
    main()

