"""
End-of-Service Award (EOS) Calculator for the Kingdom of Saudi Arabia
======================================================================

Implements the statutory End-of-Service Award (مكافأة نهاية الخدمة) per
Articles 84 through 88 of the Saudi Labor Law (Royal Decree No. M/51 of
23/8/1426H, as amended).

Legal basis (summary):
    Article 84: Half-month wage for each of the first five years of service,
                and a full-month wage for each year thereafter. Final partial
                year is prorated.
    Article 85: Resignation under an unlimited-term contract:
                - Less than 2 years of service: no EOS.
                - 2 to less than 5 years: one-third of the Article 84 award.
                - 5 to less than 10 years: two-thirds of the Article 84 award.
                - 10+ years: full Article 84 award.
    Article 86: Resignation under a limited-term contract before its expiry:
                EOS is paid only if service exceeds 5 years; otherwise none.
                (In practice this is rare and should be flagged for review.)
    Article 87: Full EOS (no reduction) is paid in cases of:
                - force majeure beyond the worker's control,
                - the worker leaving for one of the reasons enumerated in
                  Article 81 (employer breach, assault, etc.),
                - a female worker terminating within 6 months of marriage or
                  3 months of childbirth.
    Article 88: Wage on which EOS is computed = last wage as defined in
                Article 2 (basic wage + regular fixed allowances treated as
                part of the wage, typically housing and transport when paid
                as fixed monthly amounts; commissions and percentage-based
                wages may be averaged over the last 12 months — flagged).

This module is deterministic. It does not retrieve law text; it encodes the
rules. The retrieval layer should supply the article citations in user-facing
output. Update the rule constants if and when the law is amended.

Author: Keepers / KSA Labor Law Compliance Agent
Version: 2026-05-11
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class ContractType(str, Enum):
    LIMITED = "limited"        # محدد المدة
    UNLIMITED = "unlimited"    # غير محدد المدة


class TerminationReason(str, Enum):
    """All recognised termination reasons that affect EOS computation."""

    # Employer-initiated, unlimited contract, no Article 80 cause: full EOS.
    EMPLOYER_TERMINATION_UNLIMITED = "employer_termination_unlimited"

    # Limited contract reaches its natural end: full EOS.
    EMPLOYER_TERMINATION_LIMITED_EXPIRY = "employer_termination_limited_expiry"

    # Limited contract terminated early by employer without Article 80 cause:
    # full EOS plus Article 77 indemnity (indemnity handled separately).
    EMPLOYER_TERMINATION_LIMITED_EARLY = "employer_termination_limited_early"

    # Article 85: employee resignation under an unlimited contract.
    EMPLOYEE_RESIGNATION_UNLIMITED = "employee_resignation_unlimited"

    # Article 86: employee resignation from a limited contract before expiry.
    EMPLOYEE_RESIGNATION_LIMITED_EARLY = "employee_resignation_limited_early"

    # Article 81 (with Article 87): employee leaves for employer-attributable
    # cause. Full EOS regardless of length of service.
    ARTICLE_81_EMPLOYEE_LEAVING_FOR_CAUSE = "article_81_employee_leaving_for_cause"

    # Article 80: dismissal for serious cause. No EOS.
    ARTICLE_80_DISMISSAL_FOR_CAUSE = "article_80_dismissal_for_cause"

    # Mutual agreement: full EOS by default unless parties agree otherwise
    # (any waiver of statutory rights is unenforceable — Article 8).
    MUTUAL_AGREEMENT = "mutual_agreement"

    # Article 74(2): force majeure. Full EOS per Article 87.
    FORCE_MAJEURE = "force_majeure"

    # Retirement age (60 for men, 55 for women under the GOSI/Labor Law
    # framework, subject to extension): full EOS.
    RETIREMENT = "retirement"

    # Article 74(4): death of the worker. Full EOS paid to heirs.
    DEATH = "death"

    # Article 87(c): female worker terminates within 6 months of marriage or
    # 3 months of childbirth. Full EOS.
    FEMALE_EMPLOYEE_MARRIAGE_OR_CHILDBIRTH = "female_employee_marriage_or_childbirth"


@dataclass(frozen=True)
class EOSResult:
    """Structured result of an EOS computation, suitable for tool output."""

    # Inputs echoed back for auditability.
    start_date: date
    end_date: date
    last_wage: Decimal
    contract_type: ContractType
    termination_reason: TerminationReason

    # Service computation.
    years_of_service: Decimal              # exact fractional years
    first_tier_years: Decimal              # capped at 5
    second_tier_years: Decimal             # years beyond 5

    # Award computation (in SAR).
    first_tier_award: Decimal              # half-month per first-tier year
    second_tier_award: Decimal             # full-month per second-tier year
    gross_award: Decimal                   # before modifier
    modifier: Decimal                      # 0.0, 1/3, 2/3, or 1.0
    modifier_basis: str                    # which article justifies the modifier
    net_award: Decimal                     # final entitlement

    # Annotations the orchestrator should surface.
    legal_citations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for tool-use return."""
        d = asdict(self)
        # Convert Decimals to strings for JSON safety.
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
            elif isinstance(v, date):
                d[k] = v.isoformat()
            elif isinstance(v, Enum):
                d[k] = v.value
        return d


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIRST_TIER_CAP_YEARS = Decimal("5")
FIRST_TIER_FRACTION = Decimal("0.5")    # half-month per year
SECOND_TIER_FRACTION = Decimal("1.0")   # full month per year

ONE_THIRD = Decimal("1") / Decimal("3")
TWO_THIRDS = Decimal("2") / Decimal("3")

# Article 85 resignation thresholds for unlimited contracts.
ART_85_THRESHOLD_LOW = Decimal("2")    # below 2 years → 0
ART_85_THRESHOLD_MID = Decimal("5")    # 2-5 → one-third
ART_85_THRESHOLD_HIGH = Decimal("10")  # 5-10 → two-thirds; 10+ → full

# Article 86 limited-contract resignation threshold.
ART_86_THRESHOLD = Decimal("5")

# Rounding: SAR with 2 decimals using banker-safe HALF_UP.
SAR_QUANT = Decimal("0.01")


# ---------------------------------------------------------------------------
# Service-duration computation
# ---------------------------------------------------------------------------

def _years_between(start: date, end: date) -> Decimal:
    """
    Compute exact fractional years between two Gregorian dates using the
    convention: full years counted by anniversary, remainder prorated by
    actual days in the partial final year.

    This convention is consistent with Saudi labor court practice and avoids
    the bias of dividing total days by 365.25.
    """
    if end < start:
        raise ValueError("end_date must be on or after start_date")

    # Count completed full years by anniversary.
    full_years = end.year - start.year
    anniversary = _safe_replace(start, year=start.year + full_years)
    if anniversary > end:
        full_years -= 1
        anniversary = _safe_replace(start, year=start.year + full_years)

    # Compute remainder as a fraction of the final partial year.
    next_anniversary = _safe_replace(start, year=start.year + full_years + 1)
    partial_year_days = (end - anniversary).days
    year_length_days = (next_anniversary - anniversary).days

    fraction = Decimal(partial_year_days) / Decimal(year_length_days)
    return Decimal(full_years) + fraction


def _safe_replace(d: date, year: int) -> date:
    """date.replace handling Feb 29 on non-leap years by clamping to Feb 28."""
    try:
        return d.replace(year=year)
    except ValueError:
        # Feb 29 → Feb 28 in non-leap years.
        return d.replace(year=year, day=28)


# ---------------------------------------------------------------------------
# Modifier determination (the legal heart of the calculation)
# ---------------------------------------------------------------------------

def _determine_modifier(
    reason: TerminationReason,
    contract_type: ContractType,
    years: Decimal,
) -> tuple[Decimal, str, list[str], list[str]]:
    """
    Return (modifier, basis_text, citations, notes).

    The modifier is applied to the Article 84 gross award to produce the
    net entitlement. Returns Decimal(0) when no EOS is owed.
    """
    citations: list[str] = ["Labor Law Article 84"]
    notes: list[str] = []

    # --- Full-entitlement cases (Article 87 and equivalents) ---
    if reason in {
        TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
        TerminationReason.EMPLOYER_TERMINATION_LIMITED_EXPIRY,
        TerminationReason.EMPLOYER_TERMINATION_LIMITED_EARLY,
        TerminationReason.ARTICLE_81_EMPLOYEE_LEAVING_FOR_CAUSE,
        TerminationReason.FORCE_MAJEURE,
        TerminationReason.RETIREMENT,
        TerminationReason.DEATH,
        TerminationReason.FEMALE_EMPLOYEE_MARRIAGE_OR_CHILDBIRTH,
        TerminationReason.MUTUAL_AGREEMENT,
    }:
        if reason == TerminationReason.ARTICLE_81_EMPLOYEE_LEAVING_FOR_CAUSE:
            citations.append("Labor Law Article 81")
            citations.append("Labor Law Article 87")
            notes.append(
                "Article 81 grounds (employer breach, assault, deception in "
                "contract terms, etc.) must be substantiated. Recommend "
                "documentary evidence be retained."
            )
        elif reason == TerminationReason.FEMALE_EMPLOYEE_MARRIAGE_OR_CHILDBIRTH:
            citations.append("Labor Law Article 87")
            notes.append(
                "Window is 6 months from marriage or 3 months from childbirth. "
                "Confirm the termination notice falls within the statutory window."
            )
        elif reason == TerminationReason.EMPLOYER_TERMINATION_LIMITED_EARLY:
            citations.append("Labor Law Article 77")
            notes.append(
                "Article 77 indemnity is owed in addition to EOS for early "
                "termination of a limited contract without Article 80 cause. "
                "Compute separately."
            )
        elif reason == TerminationReason.MUTUAL_AGREEMENT:
            notes.append(
                "Per Article 8, any waiver by the worker of statutory rights "
                "is void. Mutual-agreement settlements that pay less than the "
                "statutory EOS are unenforceable to the extent of the shortfall."
            )
        elif reason == TerminationReason.DEATH:
            citations.append("Labor Law Article 74(4)")
            notes.append("EOS is paid to the legal heirs of the deceased worker.")
        return Decimal("1"), "Full entitlement", citations, notes

    # --- Article 80 dismissal: no EOS ---
    if reason == TerminationReason.ARTICLE_80_DISMISSAL_FOR_CAUSE:
        citations.append("Labor Law Article 80")
        notes.append(
            "Article 80 dismissal requires the employer to prove the cause. "
            "If the dismissal is successfully challenged before a labor court "
            "and reclassified, full EOS plus compensation may become owed."
        )
        return Decimal("0"), "Article 80 dismissal for cause: no EOS", citations, notes

    # --- Article 85: employee resignation under unlimited contract ---
    if reason == TerminationReason.EMPLOYEE_RESIGNATION_UNLIMITED:
        if contract_type != ContractType.UNLIMITED:
            notes.append(
                "Termination reason inconsistent with contract type "
                "(resignation_unlimited used with non-unlimited contract). "
                "Verify inputs."
            )
        citations.append("Labor Law Article 85")
        if years < ART_85_THRESHOLD_LOW:
            return Decimal("0"), "Resignation, <2 years: no EOS (Art. 85)", citations, notes
        if years < ART_85_THRESHOLD_MID:
            return ONE_THIRD, "Resignation, 2 to <5 years: one-third (Art. 85)", citations, notes
        if years < ART_85_THRESHOLD_HIGH:
            return TWO_THIRDS, "Resignation, 5 to <10 years: two-thirds (Art. 85)", citations, notes
        return Decimal("1"), "Resignation, 10+ years: full entitlement (Art. 85)", citations, notes

    # --- Article 86: employee resignation under limited contract ---
    if reason == TerminationReason.EMPLOYEE_RESIGNATION_LIMITED_EARLY:
        citations.append("Labor Law Article 86")
        notes.append(
            "Employee early resignation from a limited contract may also "
            "trigger Article 77 indemnity owed by the employee to the employer. "
            "Compute and net separately."
        )
        if years < ART_86_THRESHOLD:
            return Decimal("0"), "Limited-contract resignation, <5 years: no EOS (Art. 86)", citations, notes
        return Decimal("1"), "Limited-contract resignation, 5+ years: full entitlement (Art. 86)", citations, notes

    # Defensive default — should be unreachable if Enum is exhaustive.
    raise ValueError(f"Unhandled termination reason: {reason}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_end_of_service(
    start_date: date,
    end_date: date,
    last_wage: Decimal | float | int | str,
    contract_type: ContractType | str,
    termination_reason: TerminationReason | str,
) -> EOSResult:
    """
    Compute the End-of-Service Award per Articles 84-88 of the Saudi Labor Law.

    Parameters
    ----------
    start_date, end_date : date
        Gregorian dates of employment commencement and termination.
    last_wage : numeric
        Last monthly wage in SAR as defined by Article 2 (basic + fixed
        regular allowances). Must be > 0.
    contract_type : ContractType
        'limited' or 'unlimited'.
    termination_reason : TerminationReason
        The legal basis for termination, which drives the modifier.

    Returns
    -------
    EOSResult
        Full structured breakdown including the gross award, modifier,
        net award, citations, and contextual notes.

    Raises
    ------
    ValueError
        If inputs are invalid (end before start, non-positive wage, etc.).
    """
    # --- Coerce inputs ---
    if isinstance(contract_type, str):
        contract_type = ContractType(contract_type)
    if isinstance(termination_reason, str):
        termination_reason = TerminationReason(termination_reason)

    wage = Decimal(str(last_wage))
    if wage <= 0:
        raise ValueError("last_wage must be > 0")

    # --- Service duration ---
    years = _years_between(start_date, end_date)

    # --- Article 84 tiered gross award ---
    first_tier = min(years, FIRST_TIER_CAP_YEARS)
    second_tier = max(Decimal("0"), years - FIRST_TIER_CAP_YEARS)

    first_tier_award = (first_tier * FIRST_TIER_FRACTION * wage)
    second_tier_award = (second_tier * SECOND_TIER_FRACTION * wage)
    gross = first_tier_award + second_tier_award

    # --- Apply modifier per Articles 85, 86, 87 ---
    modifier, basis_text, citations, notes = _determine_modifier(
        termination_reason, contract_type, years
    )
    net = gross * modifier

    # --- Round monetary fields to halalas (2dp) ---
    def q(x: Decimal) -> Decimal:
        return x.quantize(SAR_QUANT, rounding=ROUND_HALF_UP)

    return EOSResult(
        start_date=start_date,
        end_date=end_date,
        last_wage=q(wage),
        contract_type=contract_type,
        termination_reason=termination_reason,
        years_of_service=years.quantize(Decimal("0.0001")),
        first_tier_years=first_tier.quantize(Decimal("0.0001")),
        second_tier_years=second_tier.quantize(Decimal("0.0001")),
        first_tier_award=q(first_tier_award),
        second_tier_award=q(second_tier_award),
        gross_award=q(gross),
        modifier=modifier.quantize(Decimal("0.000001")),
        modifier_basis=basis_text,
        net_award=q(net),
        legal_citations=citations,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Test suite (run as `python eos_calculator.py`)
# ---------------------------------------------------------------------------

def _approx(a: Decimal, b: str | Decimal, tol: str = "0.01") -> bool:
    return abs(Decimal(a) - Decimal(b)) <= Decimal(tol)


def _run_tests() -> None:
    """Worked examples drawn from common Saudi HR scenarios."""

    print("Running EOS calculator tests…\n")
    failures = 0

    # --- Test 1: Employer termination, unlimited, 3 years, SAR 10,000 ---
    # Expected: 3 * 0.5 * 10000 = SAR 15,000
    r = calculate_end_of_service(
        start_date=date(2022, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=10000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
    )
    if _approx(r.net_award, "15000.00"):
        print(f"  PASS  Test 1 — 3yr employer termination: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 1 — expected 15000.00, got {r.net_award}")

    # --- Test 2: Employer termination, 8 years, SAR 12,000 ---
    # Expected: (5 * 0.5 + 3 * 1.0) * 12000 = 5.5 * 12000 = SAR 66,000
    r = calculate_end_of_service(
        start_date=date(2017, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=12000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
    )
    if _approx(r.net_award, "66000.00"):
        print(f"  PASS  Test 2 — 8yr employer termination: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 2 — expected 66000.00, got {r.net_award}")

    # --- Test 3: Resignation, unlimited, 1.5 years ---
    # Expected: Below 2-year threshold → 0
    r = calculate_end_of_service(
        start_date=date(2023, 1, 1),
        end_date=date(2024, 7, 1),
        last_wage=8000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_UNLIMITED,
    )
    if r.net_award == Decimal("0.00"):
        print(f"  PASS  Test 3 — resign <2yr: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 3 — expected 0.00, got {r.net_award}")

    # --- Test 4: Resignation, unlimited, 3 years, SAR 9,000 ---
    # Expected: 3 * 0.5 * 9000 * (1/3) = SAR 4,500
    r = calculate_end_of_service(
        start_date=date(2022, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=9000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_UNLIMITED,
    )
    if _approx(r.net_award, "4500.00"):
        print(f"  PASS  Test 4 — resign 3yr one-third: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 4 — expected 4500.00, got {r.net_award}")

    # --- Test 5: Resignation, unlimited, 7 years, SAR 15,000 ---
    # Gross: (5 * 0.5 + 2 * 1.0) * 15000 = 4.5 * 15000 = 67,500
    # Modifier: 2/3 → 45,000
    r = calculate_end_of_service(
        start_date=date(2018, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=15000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_UNLIMITED,
    )
    if _approx(r.net_award, "45000.00"):
        print(f"  PASS  Test 5 — resign 7yr two-thirds: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 5 — expected 45000.00, got {r.net_award}")

    # --- Test 6: Resignation, unlimited, 12 years, SAR 20,000 ---
    # Gross: (5 * 0.5 + 7 * 1.0) * 20000 = 9.5 * 20000 = 190,000
    # Modifier: 1.0 (10+ years) → 190,000
    r = calculate_end_of_service(
        start_date=date(2013, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=20000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_UNLIMITED,
    )
    if _approx(r.net_award, "190000.00"):
        print(f"  PASS  Test 6 — resign 12yr full: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 6 — expected 190000.00, got {r.net_award}")

    # --- Test 7: Article 80 dismissal ---
    # Expected: 0 regardless of service
    r = calculate_end_of_service(
        start_date=date(2015, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=10000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.ARTICLE_80_DISMISSAL_FOR_CAUSE,
    )
    if r.net_award == Decimal("0.00"):
        print(f"  PASS  Test 7 — Art.80 dismissal: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 7 — expected 0.00, got {r.net_award}")

    # --- Test 8: Partial year proration — 2.5 years ---
    # Gross: 2.5 * 0.5 * 10000 = SAR 12,500
    r = calculate_end_of_service(
        start_date=date(2022, 1, 1),
        end_date=date(2024, 7, 1),
        last_wage=10000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
    )
    # Date math: Jan 1 2022 to Jul 1 2024 = 2 years + 182/366 days (2024 leap)
    # ≈ 2.4973 years → 2.4973 * 0.5 * 10000 ≈ 12,486.34
    # Acceptance band slightly wider.
    if _approx(r.net_award, "12486.34", tol="1.00"):
        print(f"  PASS  Test 8 — 2.5yr proration: SAR {r.net_award} (~12,486)")
    else:
        failures += 1
        print(f"  FAIL  Test 8 — expected ~12,486.34, got {r.net_award}")

    # --- Test 9: Article 87 — female employee marriage window, 1 year ---
    # Expected: full EOS = 1 * 0.5 * 8000 = SAR 4,000 (no Art. 85 reduction)
    r = calculate_end_of_service(
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=8000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.FEMALE_EMPLOYEE_MARRIAGE_OR_CHILDBIRTH,
    )
    if _approx(r.net_award, "4000.00"):
        print(f"  PASS  Test 9 — Art.87 marriage/childbirth: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 9 — expected 4000.00, got {r.net_award}")

    # --- Test 10: Article 81 — full EOS even at 1 year ---
    r = calculate_end_of_service(
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=10000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.ARTICLE_81_EMPLOYEE_LEAVING_FOR_CAUSE,
    )
    if _approx(r.net_award, "5000.00"):
        print(f"  PASS  Test 10 — Art.81 leaving for cause: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 10 — expected 5000.00, got {r.net_award}")

    # --- Test 11: Limited contract resignation, 3 years (< 5yr threshold) ---
    r = calculate_end_of_service(
        start_date=date(2022, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=10000,
        contract_type=ContractType.LIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_LIMITED_EARLY,
    )
    if r.net_award == Decimal("0.00"):
        print(f"  PASS  Test 11 — Art.86 limited resign <5yr: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 11 — expected 0.00, got {r.net_award}")

    # --- Test 12: Limited contract resignation, 6 years (>= 5yr threshold) ---
    # Gross: (5 * 0.5 + 1 * 1.0) * 10000 = 3.5 * 10000 = 35,000; full modifier
    r = calculate_end_of_service(
        start_date=date(2019, 1, 1),
        end_date=date(2025, 1, 1),
        last_wage=10000,
        contract_type=ContractType.LIMITED,
        termination_reason=TerminationReason.EMPLOYEE_RESIGNATION_LIMITED_EARLY,
    )
    if _approx(r.net_award, "35000.00"):
        print(f"  PASS  Test 12 — Art.86 limited resign 6yr: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 12 — expected 35000.00, got {r.net_award}")

    # --- Test 13: Death of worker — full EOS to heirs ---
    r = calculate_end_of_service(
        start_date=date(2020, 6, 15),
        end_date=date(2025, 6, 15),
        last_wage=11000,
        contract_type=ContractType.UNLIMITED,
        termination_reason=TerminationReason.DEATH,
    )
    # 5 years exactly: 5 * 0.5 * 11000 = 27,500
    if _approx(r.net_award, "27500.00"):
        print(f"  PASS  Test 13 — death, 5yr: SAR {r.net_award}")
    else:
        failures += 1
        print(f"  FAIL  Test 13 — expected 27500.00, got {r.net_award}")

    # --- Test 14: Invalid input — negative wage ---
    try:
        calculate_end_of_service(
            start_date=date(2022, 1, 1),
            end_date=date(2025, 1, 1),
            last_wage=-100,
            contract_type=ContractType.UNLIMITED,
            termination_reason=TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
        )
        failures += 1
        print("  FAIL  Test 14 — expected ValueError on negative wage")
    except ValueError:
        print("  PASS  Test 14 — rejects negative wage")

    # --- Test 15: Invalid input — end before start ---
    try:
        calculate_end_of_service(
            start_date=date(2025, 1, 1),
            end_date=date(2022, 1, 1),
            last_wage=10000,
            contract_type=ContractType.UNLIMITED,
            termination_reason=TerminationReason.EMPLOYER_TERMINATION_UNLIMITED,
        )
        failures += 1
        print("  FAIL  Test 15 — expected ValueError on reversed dates")
    except ValueError:
        print("  PASS  Test 15 — rejects reversed dates")

    print()
    if failures == 0:
        print(f"All tests passed.")
    else:
        print(f"{failures} test(s) failed.")


if __name__ == "__main__":
    _run_tests()
