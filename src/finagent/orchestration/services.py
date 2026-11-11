"""Services available to graph nodes (built once per worker). Nodes are deterministic orchestration
code: they reach sources only through the tool gateway and models only through the model gateway."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from finagent.agents.gateway import ModelGateway
from finagent.agents.providers import AzureOpenAIProvider, MockModelProvider, ModelProvider
from finagent.core.config import Settings
from finagent.documents.runtime import build_document_scanner, build_store, sandbox_config
from finagent.integrations.factory import ConnectorFactory
from finagent.integrations.tool_gateway.gateway import ToolGateway
from finagent.services.document_service.ingest import DocumentPorts
from finagent.services.document_service.processing import ProcessingConfig
from finagent.services.knowledge_service.retrieval import Retriever, build_retriever


def build_provider(settings: Settings) -> ModelProvider:
    if (
        settings.model_provider == "azure_openai"
        and settings.azure_openai_endpoint
        and settings.azure_openai_deployment
        and settings.azure_openai_api_key
    ):
        return AzureOpenAIProvider(
            settings.azure_openai_endpoint,
            settings.azure_openai_deployment,
            settings.azure_openai_api_version,
            settings.azure_openai_api_key.get_secret_value(),
        )
    return MockModelProvider()


@dataclass
class GraphServices:
    settings: Settings
    tool_gateway: ToolGateway
    model_gateway: ModelGateway
    retriever: Retriever
    ports: DocumentPorts
    processing: ProcessingConfig


def build_services(
    factory: sessionmaker[Session], settings: Settings, provider: ModelProvider | None = None
) -> GraphServices:
    return GraphServices(
        settings=settings,
        tool_gateway=ToolGateway(factory, ConnectorFactory(settings, factory), settings),
        model_gateway=ModelGateway(factory, provider or build_provider(settings), settings),
        retriever=build_retriever(settings),
        ports=DocumentPorts(build_store(settings), build_document_scanner(settings)),
        processing=ProcessingConfig(sandbox_config(settings), settings.document_max_attempts),
    )
