"""MCP server: yalnız oxuma tool-ları, yeni admin API-yə gedir."""
import pytest

import mcp_server.server as server

pytestmark = pytest.mark.unit


def test_tools_call_the_read_only_admin_api(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_get", lambda path, **params: calls.append((path, params)) or [])
    server.list_appeals(status="new", limit=5)
    server.get_appeal("mr-2026-000001")
    server.overdue_appeals()
    server.appeal_stats()
    server.knowledge_gaps(days=7)
    server.weekly_report()
    assert calls == [("/admin/appeals", {"status": "new", "limit": 5}), ("/admin/appeals/MR-2026-000001", {}),
                     ("/admin/appeals/overdue", {}), ("/admin/stats", {}),
                     ("/admin/knowledge-gaps", {"days": 7, "limit": 50}), ("/admin/report", {"days": 7})]


def test_the_only_write_tool_is_a_proposal(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_post", lambda path, body: calls.append((path, body)) or {"sent_to": 2})
    assert server.propose_assignment("MR-2026-000001", 5, "təcrübə") == {"sent_to": 2}
    assert calls == [("/admin/proposals/assign", {"number": "MR-2026-000001", "staff_id": 5, "reason": "təcrübə"})]


def test_there_is_no_tool_that_changes_appeals():
    # Cavab yalnız əməkdaş botunda, insan tərəfindən yazılır (spec 9-cu bölmə)
    assert not hasattr(server, "answer_appeal")
