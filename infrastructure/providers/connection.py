"""Provider connection option helpers reserved for future CLI /connect flow."""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.providers.catalog import list_provider_definitions


@dataclass(frozen=True)
class ConnectOption:
    provider_id: str
    display_name: str
    requires_base_url: bool = False


class ProviderConnectionService:
    def list_connect_options(self) -> tuple[ConnectOption, ...]:
        return tuple(
            ConnectOption(
                provider_id=provider.id,
                display_name=provider.display_name,
                requires_base_url=provider.requires_base_url,
            )
            for provider in list_provider_definitions()
        )
