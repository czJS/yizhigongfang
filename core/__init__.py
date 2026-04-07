"""
core/: cross-layer shared utilities (lightweight).

Goals:
- No Flask/Electron dependency
- No heavy ML dependency (WhisperX/Torch/etc.)
- Safe to import from both:
  - apps/backend/backend (API layer)
  - pipelines/* (pipeline execution layer)
  - apps/worker_quality (packaged worker)
"""

