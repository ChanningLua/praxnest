"""HTTP route modules. Mounted under ``/api``.

Each submodule exports ``router`` (an APIRouter); ``app.create_app``
imports + includes them. Splitting per-resource keeps app.py skimmable.
"""

from .ai import router as ai_router
from .attachments import router as attachments_router, serve_router as attachments_serve_router
from .audit import router as audit_router
from .auth import router as auth_router
from .memory import router as memory_router, cross_router as memory_cross_router
from .notes import router as notes_router, search_router as notes_search_router
from .notify import router as notify_router
from .workspaces import router as workspaces_router

__all__ = [
    "ai_router",
    "attachments_router",
    "attachments_serve_router",
    "audit_router",
    "auth_router",
    "memory_router",
    "memory_cross_router",
    "notes_router",
    "notes_search_router",
    "notify_router",
    "workspaces_router",
]
