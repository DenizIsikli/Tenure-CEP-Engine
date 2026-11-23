"""Pure business rules for research and funding decisions."""
from __future__ import annotations

from typing import Any


def _amount(value: Any, field_name: str) -> float:
    if value is None or value == "":
        raise ValueError(f"{field_name} is required")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return amount


def assess_research(research_importance: Any, threshold: float = 5.0) -> dict[str, Any]:
    """Current Department's research importance threshold (0-10 scale)."""
    if research_importance is None or research_importance == "":
        return {
            "researchFitScore": None,
            "researchThreshold": float(threshold),
            "academicJustificationApproved": False,
            "academicDecisionReason": "Research importance was not provided.",
        }
    try:
        score = float(research_importance)
    except (TypeError, ValueError) as exc:
        raise ValueError("researchImportance must be between 0 and 10") from exc
    if not 0 <= score <= 10:
        raise ValueError("researchImportance must be between 0 and 10")
    passed = score >= float(threshold)
    return {
        "researchFitScore": score,
        "researchThreshold": float(threshold),
        "academicJustificationApproved": passed,
        "academicDecisionReason": (
            "Research importance meets the required threshold."
            if passed else "Research importance is below the required threshold."
        ),
    }


def calculate_funding(
    professor_salary: Any,
    future_department_funding: Any,
    current_department_available_funding: Any,
) -> dict[str, Any]:
    """Calculate a notional funding split; no money is transferred."""
    salary = _amount(professor_salary, "professorSalary")
    future_funding = _amount(future_department_funding, "futureDepartmentFunding")
    current_available = _amount(
        current_department_available_funding,
        "currentDepartmentAvailableFunding",
    )
    if salary <= 0:
        raise ValueError("professorSalary must be greater than zero")

    shortfall = max(0.0, salary - future_funding)
    current_contribution = min(shortfall, current_available)
    total = future_funding + current_contribution
    current_can_cover = current_available >= shortfall
    verified = total >= salary
    if shortfall == 0:
        reason = "The Future Department can cover the full salary."
    elif current_can_cover:
        reason = "The Current Department can cover the shortfall; the Dean must review the contribution."
    else:
        reason = "Combined available funding is insufficient to cover the salary."

    return {
        "futureDepartmentCanCoverSalary": future_funding >= salary,
        "canFundWithoutCurrentDepartment": future_funding >= salary,
        "requiresCurrentDepartmentContribution": shortfall > 0,
        "fundingShortfall": shortfall,
        "currentDepartmentContributionRequired": shortfall,
        "currentDepartmentContributionProposed": current_contribution,
        "currentDepartmentCanCoverShortfall": current_can_cover,
        "totalFundingAvailable": total,
        "fundingArrangementVerified": verified,
        "fundingDecisionReason": reason,
    }


def dean_decision(
    future_department_suitable: Any,
    academic_justification_approved: Any,
    funding_arrangement_verified: Any,
    dean_academic_fit: Any = None,
    dean_funding_approved: Any = None,
) -> dict[str, Any]:
    """Final Dean gate; all earlier assessments are considered, not short-circuited."""
    suitability_ok = future_department_suitable is True
    research_ok = academic_justification_approved is True
    funding_ok = funding_arrangement_verified is True
    if dean_academic_fit is False:
        research_ok = False
    if dean_funding_approved is False:
        funding_ok = False

    approved = suitability_ok and research_ok and funding_ok
    reasons: list[str] = []
    if not suitability_ok:
        reasons.append("The Future Department did not consider the professor suitable for its research domain.")
    if not research_ok:
        reasons.append("The Current Department's research threshold or the Dean's academic review was not passed.")
    if not funding_ok:
        reasons.append("Combined proposed funding is insufficient or the Dean did not approve the funding proposal.")
    if approved:
        reasons.append("Domain fit, research assessment and funding checks passed; the transfer is approved.")

    return {
        "deanAcademicFit": research_ok,
        "deanFundingApproved": funding_ok,
        "futureDepartmentSuitable": suitability_ok,
        "approved": approved,
        "decisionReason": " ".join(reasons),
    }
