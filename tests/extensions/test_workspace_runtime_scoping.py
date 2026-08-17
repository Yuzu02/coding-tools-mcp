from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from coding_tools_mcp.extensions import (
    CORE_WORKSPACE_RUNTIMES,
    ExtensionManifest,
    ExtensionRegistry,
    RuntimeConfig,
    SchemaPatch,
    ToolDecorator,
)
from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions.contributions import ToolHandler
from coding_tools_mcp.server import Runtime


class ScopedReadProbe:
    manifest = ExtensionManifest(name="scoped-read-probe")
    roots: dict[str, Path] = {}

    def __init__(self) -> None:
        self._service = None
        self._handles: dict[str, object] = {}

    def configure(self, config) -> None:
        return None

    def prepare(self) -> None:
        return None

    def register(self, context) -> None:
        service = context.services.require(CORE_WORKSPACE_RUNTIMES)
        self._service = service
        self._handles = {name: service.create(root) for name, root in self.roots.items()}

        def wrap(next_handler: ToolHandler) -> ToolHandler:
            def routed(args: dict[str, Any]) -> dict[str, Any]:
                clean = dict(args)
                target = str(clean.pop("target"))
                if target == "nested":
                    def outer(_args: dict[str, Any]) -> dict[str, Any]:
                        before = next_handler(clean)["content"]
                        inner = service.invoke(self._handles["b"], next_handler, clean)["content"]
                        after = next_handler(clean)["content"]
                        return {"before": before, "inner": inner, "after": after}

                    return service.invoke(self._handles["a"], outer, {})
                if target == "boom":
                    def fail(_args: dict[str, Any]) -> dict[str, Any]:
                        raise RuntimeError("scoped probe failure")

                    return service.invoke(self._handles["a"], fail, {})
                return service.invoke(self._handles[target], next_handler, clean)

            return routed

        context.add_decorator(
            ToolDecorator(
                targets=("read_file",),
                schema_patch=SchemaPatch(
                    properties={
                        "target": {
                            "type": "string",
                            "enum": ["a", "b", "nested", "boom"],
                        }
                    },
                    required=("target",),
                ),
                wrap_handler=wrap,
            )
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        if self._service is None:
            return
        for handle in self._handles.values():
            self._service.close(handle)


class WorkspaceRuntimeScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        for directory, content in (
            (self.bootstrap, "BOOTSTRAP\n"),
            (self.alpha, "A\n"),
            (self.beta, "B\n"),
        ):
            directory.mkdir()
            (directory / "same.txt").write_text(content, encoding="utf-8")
            (directory / "pyproject.toml").write_text(
                "[project]\nname='fixture'\nversion='0'\n",
                encoding="utf-8",
            )
        ScopedReadProbe.roots = {"a": self.alpha, "b": self.beta}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self) -> Runtime:
        registry = ExtensionRegistry([ScopedReadProbe], default_enabled=())
        return Runtime(
            self.bootstrap,
            extension_registry=registry,
            extension_config=RuntimeConfig.defaults(enabled=("scoped-read-probe",)),
        )

    def test_same_relative_path_routes_to_selected_workspace_state(self) -> None:
        runtime = self.runtime()
        try:
            alpha = runtime.call_tool("read_file", {"target": "a", "path": "same.txt"})
            beta = runtime.call_tool("read_file", {"target": "b", "path": "same.txt"})

            self.assertEqual(alpha["structuredContent"]["content"], "A\n")
            self.assertEqual(beta["structuredContent"]["content"], "B\n")
        finally:
            runtime.close()

    def test_binding_resets_after_handler_failure(self) -> None:
        runtime = self.runtime()
        try:
            failed = runtime.call_tool("read_file", {"target": "boom", "path": "same.txt"})
            self.assertTrue(failed["isError"])
            self.assertEqual(failed["structuredContent"]["error"]["code"], "INTERNAL_ERROR")

            bootstrap = runtime.read_file({"path": "same.txt"})
            self.assertEqual(bootstrap["content"], "BOOTSTRAP\n")
        finally:
            runtime.close()

    def test_nested_binding_restores_outer_workspace(self) -> None:
        runtime = self.runtime()
        try:
            result = runtime.call_tool("read_file", {"target": "nested", "path": "same.txt"})
            payload = result["structuredContent"]
            self.assertEqual(
                {"before": payload["before"], "inner": payload["inner"], "after": payload["after"]},
                {"before": "A\n", "inner": "B\n", "after": "A\n"},
            )
        finally:
            runtime.close()

    def test_concurrent_bindings_do_not_cross_contaminate(self) -> None:
        runtime = self.runtime()
        try:
            targets = ["a", "b"] * 50

            def read(target: str) -> tuple[str, str]:
                result = runtime.call_tool("read_file", {"target": target, "path": "same.txt"})
                return target, str(result["structuredContent"]["content"])

            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(read, targets))

            for target, content in results:
                self.assertEqual(content, "A\n" if target == "a" else "B\n")
        finally:
            runtime.close()

    def test_workspace_runtime_service_validates_resolves_and_closes_handles(self) -> None:
        runtime = Runtime(
            self.bootstrap,
            extension_config=RuntimeConfig.defaults(enabled=()),
        )
        service = runtime.workspace_runtime_service
        try:
            self.assertEqual(service.validate_root(self.alpha), self.alpha.resolve())
            missing = self.root / "future-project"
            self.assertEqual(
                service.validate_root(missing, require_exists=False),
                missing.resolve(strict=False),
            )
            with self.assertRaises(ToolFailure):
                service.validate_root(missing)

            handle = service.create(self.alpha)
            resolved = service.resolve_existing(handle, "same.txt")
            self.assertEqual(resolved.display, "same.txt")
            self.assertEqual(resolved.path, (self.alpha / "same.txt").resolve())

            service.close(handle)
            service.close(handle)
            with self.assertRaisesRegex(ValueError, "another runtime"):
                service.resolve_existing(handle, "same.txt")
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
