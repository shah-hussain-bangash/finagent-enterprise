"""Per-tenant connector construction (DIR-002, DIR-021; SPR-007 connector-specific credentials).

A tenant's connectors use only that tenant's credential (looked up by tenant slug from the trusted
database row, never from a request). Circuit breakers are shared per (tenant, source) so one
tenant's failing source cannot open another tenant's circuit (NQ-028 isolation).
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from functools import partial

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from finagent.core.config import Settings
from finagent.database.models import Tenant
from finagent.database.session import transaction
from finagent.integrations.http_client import ConnectorHttpClient
from finagent.integrations.notifications.port import (
    DeliveryStatus,
    Notification,
    NotificationError,
)
from finagent.integrations.observations import record_observation
from finagent.integrations.resilience import (
    CircuitBreaker,
    ConnectorError,
    ErrorKind,
    RetryPolicy,
)
from finagent.integrations.sources import (
    CustomerKycConnector,
    DocumentConnector,
    HttpNotificationAdapter,
    PolicyConnector,
    SanctionsConnector,
    TransactionConnector,
)

SOURCE = "core-banking"
_BREAKERS: dict[tuple[uuid.UUID, str], CircuitBreaker] = {}
_LOCK = threading.Lock()


class ConnectorFactory:
    def __init__(
        self, settings: Settings, factory: sessionmaker[Session], http: httpx.Client | None = None
    ) -> None:
        self._s, self._factory, self._http = settings, factory, http
        self._creds = settings.connector_credentials_map()
        self._clients: dict[uuid.UUID, ConnectorHttpClient] = {}

    def _breaker(self, tenant_id: uuid.UUID) -> CircuitBreaker:
        with _LOCK:
            return _BREAKERS.setdefault(
                (tenant_id, SOURCE),
                CircuitBreaker(self._s.connector_circuit_failures, self._s.connector_circuit_reset_seconds),
            )

    def client(self, tenant_id: uuid.UUID) -> ConnectorHttpClient:
        if tenant_id in self._clients:
            return self._clients[tenant_id]
        with transaction(self._factory) as s:
            slug = s.scalar(select(Tenant.slug).where(Tenant.id == tenant_id, Tenant.status == "ACTIVE"))
        key = self._creds.get(slug or "")
        if not key:  # no credential configured for this tenant: the connector is not enabled
            raise ConnectorError(ErrorKind.UNAUTHORIZED, SOURCE)
        client = ConnectorHttpClient(
            SOURCE,
            self._s.bank_base_url,
            key,
            timeout_s=self._s.connector_timeout_seconds,
            policy=RetryPolicy(max_attempts=self._s.connector_max_attempts),
            breaker=self._breaker(tenant_id),
            http=self._http,
            observe=partial(record_observation, self._factory, tenant_id, SOURCE),
        )
        self._clients[tenant_id] = client
        return client

    @property
    def max_age(self) -> timedelta:
        return timedelta(hours=self._s.source_max_age_hours)

    def customers(self, tenant_id: uuid.UUID) -> CustomerKycConnector:
        return CustomerKycConnector(self.client(tenant_id), self.max_age)

    def transactions(self, tenant_id: uuid.UUID) -> TransactionConnector:
        return TransactionConnector(self.client(tenant_id), self.max_age)

    def sanctions(self, tenant_id: uuid.UUID) -> SanctionsConnector:
        return SanctionsConnector(self.client(tenant_id), self.max_age)

    def policies(self, tenant_id: uuid.UUID) -> PolicyConnector:
        return PolicyConnector(self.client(tenant_id), self.max_age)

    def documents(self, tenant_id: uuid.UUID) -> DocumentConnector:
        return DocumentConnector(self.client(tenant_id), self._s.document_max_bytes)


class BankNotificationRouter:
    """NotificationPort that delivers through each tenant's own bank connector credential."""

    def __init__(self, connectors: ConnectorFactory) -> None:
        self._connectors = connectors

    def _adapter(self, tenant_id: uuid.UUID) -> HttpNotificationAdapter:
        return HttpNotificationAdapter(self._connectors.client(tenant_id))

    def send(self, notification: Notification) -> None:
        try:
            adapter = self._adapter(notification.tenant_id)
        except ConnectorError as exc:
            raise NotificationError(exc.kind.value, transient=False) from exc
        adapter.send(notification)

    def status(self, tenant_id: uuid.UUID, idempotency_key: str) -> DeliveryStatus:
        try:
            adapter = self._adapter(tenant_id)
        except ConnectorError:
            return DeliveryStatus.UNKNOWN
        return adapter.status(tenant_id, idempotency_key)


def reset_breakers() -> None:
    """Tests only: forget circuit state between independent scenarios."""
    with _LOCK:
        _BREAKERS.clear()
