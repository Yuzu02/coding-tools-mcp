"""Single-workspace project and skill discovery extension."""

from .extension import PROJECT_CATALOG, ProjectsExtension
from .registry import (
    PROJECT_ID_RE,
    PROJECT_REGISTRY,
    ProjectRegistry,
    ProjectRegistryError,
    RegisteredProject,
    build_project_registry,
)

__all__ = [
    "PROJECT_CATALOG",
    "PROJECT_ID_RE",
    "PROJECT_REGISTRY",
    "ProjectRegistry",
    "ProjectRegistryError",
    "ProjectsExtension",
    "RegisteredProject",
    "build_project_registry",
]
