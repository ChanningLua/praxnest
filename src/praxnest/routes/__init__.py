"""HTTP route modules. Mounted under ``/api``.

Each submodule exports ``router`` (an APIRouter); ``app.create_app``
imports + includes them. Splitting per-resource keeps app.py skimmable.
"""

from .audit import router as audit_router
from .auth import router as auth_router
from .notes import router as notes_router, search_router as notes_search_router
from .workspaces import router as workspaces_router

__all__ = [
    "audit_router",
    "auth_router",
    "notes_router",
    "notes_search_router",
    "workspaces_router",
]
