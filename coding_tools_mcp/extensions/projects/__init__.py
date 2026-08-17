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
from .runtime import PROJECT_RUNTIMES, CommandOwnershipIndex, ProjectRuntime, ProjectRuntimeManager

__all__ = [
    "PROJECT_CATALOG",
    "PROJECT_ID_RE",
    "PROJECT_REGISTRY",
    "PROJECT_RUNTIMES",
    "CommandOwnershipIndex",
    "ProjectRegistry",
    "ProjectRegistryError",
    "ProjectsExtension",
    "ProjectRuntime",
    "ProjectRuntimeManager",
    "RegisteredProject",
    "build_project_registry",
]
