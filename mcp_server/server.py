"""MCP server (#16): rəhbər və koordinator Claude Desktop / Claude Code-dan müraciətlərin vəziyyətinə baxır.

Məs: "Bu gün gecikən müraciətləri göstər və şöbələr üzrə qruplaşdır", "Son ay AI hansı suallara cavab tapmadı?"

Müraciəti dəyişən tool yoxdur. `propose_assignment` yalnız təklifdir: koordinator əməkdaş botunda təsdiqləyir.
Cavab yalnız əməkdaş botunda insan tərəfindən yazılır.
Admin API mətnləri maskalanmış qaytarır, çünki bu cavablar LLM-ə gedir.

İşə salmaq (stdio):  MURACIET_API_URL=http://localhost:8000 ADMIN_API_KEY=... python -m mcp_server.server
"""
import os

import httpx
from mcp.server.mcpserver import MCPServer

API = os.getenv("MURACIET_API_URL", "http://localhost:8000")
HEADERS = {"X-API-Key": os.getenv("ADMIN_API_KEY", "")}

mcp = MCPServer("muraciet-ai", instructions="Vətəndaş müraciətlərinin vəziyyəti (yalnız oxuma). "
                                            "[SOYAD_1] kimi ifadələr gizlədilmiş şəxsi məlumatdır.")


def _get(path: str, **params):
    r = httpx.get(f"{API}{path}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict):
    r = httpx.post(f"{API}{path}", headers=HEADERS, json=body, timeout=30)
    if r.status_code in (404, 409, 422):  # LLM-ə səbəbi göstərilsin
        return {"error": r.json().get("detail", r.text)}
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_appeals(status: str | None = None, limit: int = 20) -> list[dict]:
    """Müraciətlərin siyahısı (yalnız metadata). status: new | assigned | waiting_citizen | pending_approval |
    answered | closed | redirected | not_reviewed"""
    return _get("/admin/appeals", **({"status": status} if status else {}), limit=limit)


@mcp.tool()
def get_appeal(number: str) -> dict:
    """Müraciətin maskalanmış təfərrüatı və vəziyyət tarixçəsi (məs: MR-2026-000123)."""
    return _get(f"/admin/appeals/{number.upper()}")


@mcp.tool()
def overdue_appeals() -> list[dict]:
    """Baxılma müddəti keçmiş açıq müraciətlər."""
    return _get("/admin/appeals/overdue")


@mcp.tool()
def appeal_stats() -> dict:
    """Statuslar üzrə say və müddəti keçənlərin sayı."""
    return _get("/admin/stats")


@mcp.tool()
def weekly_report(days: int = 7) -> dict:
    """Son N günün hesabatı: göstəricilər (saylar, şöbələr, müddəti keçənlər, 👍/👎) və AI təhlili."""
    return _get("/admin/report", days=days)


@mcp.tool()
def knowledge_gaps(days: int = 30, limit: int = 50) -> list[dict]:
    """AI-nin bilik bazasında cavab tapmadığı suallar və neçə dəfə verildikləri (bilik bazasına nə əlavə
    olunmalıdır). Mətnlər maskalanıb."""
    return _get("/admin/knowledge-gaps", days=days, limit=limit)


@mcp.tool()
def list_staff() -> list[dict]:
    """Müraciət təyin oluna bilən aktiv əməkdaşlar: id, ad, rol, şöbə və açıq işlərinin sayı."""
    return _get("/admin/staff")


@mcp.tool()
def propose_assignment(number: str, staff_id: int, reason: str = "") -> dict:
    """Müraciəti əməkdaşa təyin etməyi TƏKLİF edir: koordinatorlar əməkdaş botunda "✅ Təyin et" kartı alır və
    qərarı onlar verir. Müraciət dərhal dəyişmir. staff_id: list_staff-dan."""
    return _post("/admin/proposals/assign", {"number": number, "staff_id": staff_id, "reason": reason})


if __name__ == "__main__":
    mcp.run()
