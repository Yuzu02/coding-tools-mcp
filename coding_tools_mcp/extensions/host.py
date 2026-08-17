from __future__ import annotations

from typing import Any, Iterable, Mapping

from .api import Extension, ExtensionContext
from .config import RuntimeConfig
from .contributions import ComposedTool, ContributionRegistry, compose_tools
from .registry import ExtensionRegistry
from .services import CapabilityKey, ServiceRegistry


class ExtensionHost:
    def __init__(
        self,
        *,
        order: tuple[str, ...],
        instances: Mapping[str, Extension],
        services: ServiceRegistry,
        contributions: ContributionRegistry,
        tools: Mapping[str, ComposedTool],
    ) -> None:
        self._order = order
        self._instances = dict(instances)
        self._services = services
        self._contributions = contributions
        self._tools = tools
        self._stopped = False

    @classmethod
    def build(
        cls,
        *,
        registry: ExtensionRegistry,
        config: RuntimeConfig,
        core_tools: Mapping[str, ComposedTool],
        seed_services: Iterable[tuple[CapabilityKey[Any], Any]] = (),
    ) -> "ExtensionHost":
        order = registry.resolve_order(config.enabled_extensions)
        instances = {name: registry.extension_type(name)() for name in order}
        services = ServiceRegistry()
        contributions = ContributionRegistry()
        for key, value in seed_services:
            services.provide(key, value)
        for name in order:
            instances[name].configure(config.extension(name))

        prepared: list[str] = []
        for name in order:
            prepared.append(name)
            try:
                instances[name].prepare()
            except Exception as exc:
                warnings = cls._stop_instances(instances, reversed(prepared))
                for warning in warnings:
                    exc.add_note(f"extension cleanup: {warning}")
                raise

        try:
            for name in order:
                instances[name].register(
                    ExtensionContext(
                        services=services,
                        contributions=contributions,
                        extension_name=name,
                    )
                )
            tools = compose_tools(core_tools, contributions, order)
            contributions.freeze()
            services.freeze()
        except Exception as exc:
            warnings = cls._stop_instances(instances, reversed(prepared))
            for warning in warnings:
                exc.add_note(f"extension cleanup: {warning}")
            raise

        host = cls(
            order=order,
            instances=instances,
            services=services,
            contributions=contributions,
            tools=tools,
        )
        started: list[str] = []
        for name in order:
            try:
                instances[name].start()
            except Exception as exc:
                warnings = host._stop_names(reversed(order))
                for warning in warnings:
                    exc.add_note(f"extension cleanup: {warning}")
                host._stopped = True
                raise
            started.append(name)
        return host

    @property
    def tools(self) -> Mapping[str, ComposedTool]:
        return self._tools

    def metadata(self) -> dict[str, object]:
        return {
            "enabled": list(self._order),
            "order": list(self._order),
            "contributions": {
                "tools": [tool.name for _extension, tool in self._contributions.tool_entries()],
                "decorated_tools": sorted(
                    {
                        target
                        for _extension, decorator in self._contributions.decorator_entries()
                        for target in decorator.targets
                    }
                ),
            },
            "metadata": self._contributions.metadata_snapshot(),
        }

    def server_instructions(self, default_text: str) -> str:
        return self._contributions.compose_server_instructions(default_text, self._order)

    @staticmethod
    def _stop_instances(
        instances: Mapping[str, Extension],
        names: Iterable[str],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        for name in names:
            try:
                instances[name].stop()
            except Exception as exc:
                if len(warnings) < 32:
                    warnings.append(f"{name}: {exc}")
        return tuple(warnings)

    def _stop_names(self, names: Iterable[str]) -> tuple[str, ...]:
        return self._stop_instances(self._instances, names)

    def stop(self) -> tuple[str, ...]:
        if self._stopped:
            return ()
        self._stopped = True
        return self._stop_names(reversed(self._order))
