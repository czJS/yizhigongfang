# apps/worker_quality

Repo layout v2 placeholder.

- **Current implementation (transition)**: worker entrypoint still lives at `backend/quality_worker_entry.py`.
- **Packaging entry**: `quality_worker.exe` is built from `backend/quality_worker_entry.py` via `packaging/pyinstaller/quality_worker.spec`.

Stage C will migrate code into `apps/worker_quality/` and remove legacy paths.

