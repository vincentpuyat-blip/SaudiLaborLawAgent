"""
Tool dispatcher for the KSA Labor Law agent.

Maps tool-use requests from Claude to deterministic Python functions.
Only `calculate_end_of_service` is wired to a real implementation today;
the remaining tools defined in tool_schemas.json are stubs that return
{"error": "Not yet implemented"} so the agent can degrade gracefully.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from eos_calculator import (
    ContractType,
    TerminationReason,
    calculate_end_of_service,
)


def _calculate_end_of_service(args: dict[str, Any]) -> dict[str, Any]:
    # The schema requires gregorian dates as ISO strings. If the caller
    # specifies calendar='hijri', flag it as unsupported until conversion
    # is wired up rather than silently mis-computing.
    calendar = args.get("calendar", "gregorian")
    if calendar == "hijri":
        return {
            "error": "Hijri calendar conversion is not yet implemented. "
                     "Please supply Gregorian equivalents for start_date and end_date."
        }

    try:
        start = date.fromisoformat(args["start_date"])
        end = date.fromisoformat(args["end_date"])
        result = calculate_end_of_service(
            start_date=start,
            end_date=end,
            last_wage=args["last_wage"],
            contract_type=ContractType(args["contract_type"]),
            termination_reason=TerminationReason(args["termination_reason"]),
        )
        return result.to_dict()
    except (ValueError, KeyError) as exc:
        return {"error": f"Invalid input: {exc}"}


def _not_implemented(_: dict[str, Any]) -> dict[str, Any]:
    return {"error": "Not yet implemented"}


DISPATCH: dict[str, Any] = {
    "calculate_end_of_service": _calculate_end_of_service,
    "calculate_gosi_contributions": _not_implemented,
    "calculate_notice_period": _not_implemented,
    "calculate_annual_leave": _not_implemented,
    "calculate_overtime": _not_implemented,
    "check_nitaqat_compliance": _not_implemented,
    "retrieve_legal_provisions": _not_implemented,
}


def run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call from Claude to its Python implementation."""
    handler = DISPATCH.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    return handler(args)
