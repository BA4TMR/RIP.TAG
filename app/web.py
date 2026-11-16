"""Template rendering and HTML response helpers."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.models import Member
from app.utils import iso_utc


def render(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    templates = request.app.state.templates
    settings = request.app.state.settings
    base_context: dict[str, object] = {
        "request": request,
        "app_name": settings.app_name,
        "format_time": iso_utc,
    }
    base_context.update(context)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=base_context,
        status_code=status_code,
    )


def error_page(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return render(
        request,
        "error.html",
        status_code=status_code,
        error_status=status_code,
        title=title,
        message=message,
    )


def member_context(member: Member) -> dict:
    group = member.group or SimpleNamespace(name=member.group_no, color=member.group_color)
    return {
        "member": member,
        "group": group,
        "status_label": {
            "active": "存活中",
            "eliminated": "已淘汰",
            "unregistered": "未注册",
        }.get(member.status, member.status),
    }
