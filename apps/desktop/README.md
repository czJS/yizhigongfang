# apps/desktop

Repo layout v2 placeholder.

- **Current implementation (transition)**: desktop app still lives under `frontend/`.
- **Windows packaging** uses `packaging/scripts/windows/build_installer.ps1`, which will detect `apps/desktop/` when it is migrated, and otherwise falls back to `frontend/`.

Stage A created stable entrypoints under `packaging/` first; Stage C will complete the directory migration.

