"""Retention-safe evidence reads do not rewrite immutable parent content."""

from typing import Any
from uuid import uuid4

import pytest

from finagent.database.models import EvidenceItem
from finagent.services.evidence_service.items import evidence_content


@pytest.mark.req("FR-039", "SPR-024")
@pytest.mark.parametrize("content", [None, {}, {"status": "PARSED"}])
def test_evidence_content_preserves_parent(content: dict[str, Any] | None) -> None:
    item = EvidenceItem(id=uuid4(), content=content)
    result = evidence_content(item)
    assert result == ({} if content is None else content)
    assert item.content is content
    if content is not None:
        assert result is content
