"""DocEngine: canonical document model, patch operations, deterministic renderers and validators."""
from .model import Document, detect_script, new_block_id
from .patch import PatchError, PatchResult, VersionConflict, apply_patch, parse_ops
from .renderers import FORMATS, RenderedFile, RendererUnavailable, render
from .validators import validate

__all__ = [
    "FORMATS", "Document", "PatchError", "PatchResult", "RenderedFile", "RendererUnavailable", "VersionConflict",
    "apply_patch", "detect_script", "new_block_id", "parse_ops", "render", "validate",
]
