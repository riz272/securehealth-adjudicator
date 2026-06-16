"""Pydantic data models for claims, policy rules, and settlements.

These models are the contract between extraction (LLM-driven, untrusted)
and adjudication (deterministic, pure calculation). Anything that crosses
from raw PDF text into the calculation engine must pass through one of
these models first.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class NetworkStatus(str, Enum):
    IN_NETWORK = "In-Network"
    OUT_OF_NETWORK = "Out-of-Network"


class PreAuthStatus(str, Enum):
    """Pre-authorisation status for a claim.

    NA means pre-auth is not applicable to this benefit type at all
    (e.g. an outpatient consultation), as distinct from YES/NO which
    means pre-auth was/was not obtained for a benefit that requires it.
    """

    YES = "Yes"
    NO = "No"
    NA = "n/a"


# ---------------------------------------------------------------------------
# Claim input
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    claim_id: str
    service_date: date
    benefit: str = Field(..., description="Must match a key in PolicyConfig.benefits")
    network: NetworkStatus
    billed_amount: float = Field(..., gt=0)
    pre_auth_obtained: PreAuthStatus = PreAuthStatus.NA
    diagnosis_note: str = ""
    is_chronic_related: bool = Field(
        default=False,
        description="True if this claim relates to a declared chronic/pre-existing condition",
    )
    is_elective: bool = Field(
        default=False,
        description="True if the treatment was elective (non-emergency). Relevant to pre-auth penalty.",
    )

    @model_validator(mode="after")
    def _billed_amount_two_dp(self) -> "Claim":
        self.billed_amount = round(self.billed_amount, 2)
        return self


# ---------------------------------------------------------------------------
# Policy rule structures
# ---------------------------------------------------------------------------


class NetworkTerms(BaseModel):
    """Member cost-sharing terms for one network status (in- or out-of-network)."""

    covered: bool = True
    deductible: float = 0.0
    coinsurance_pct: float = Field(..., ge=0, le=100)


class BenefitRule(BaseModel):
    benefit: str
    sub_limit: Optional[float] = Field(
        default=None, description="Annual sub-limit in AED. None means no separate sub-limit (e.g. within aggregate only)."
    )
    in_network: NetworkTerms
    out_of_network: NetworkTerms
    deductible_applies: bool = Field(
        default=False,
        description="Whether a deductible applies to this benefit at all (per policy, only Outpatient Consultation has one).",
    )


class Endorsement(BaseModel):
    endorsement_id: str
    benefit: str
    description: str = ""
    overrides: dict = Field(
        default_factory=dict,
        description=(
            "Fields to override on the target BenefitRule, e.g. "
            '{"sub_limit": 4000, "in_network": {"coinsurance_pct": 10}}'
        ),
    )


class PreAuthRule(BaseModel):
    benefits_requiring_preauth: list[str] = Field(default_factory=list)
    elective_only: bool = True
    penalty_pct_if_missing: float = Field(default=0, ge=0, le=100)


class ExclusionRules(BaseModel):
    chronic_waiting_period_months: int = 6
    general_exclusions: list[str] = Field(default_factory=list)


class CalculationStep(str, Enum):
    CAP_AT_ELIGIBLE = "cap_at_eligible_amount"
    SUBTRACT_DEDUCTIBLE = "subtract_deductible"
    APPLY_COINSURANCE = "apply_member_coinsurance"
    INSURER_PAYS_REMAINDER = "insurer_pays_remainder"


class PolicyConfig(BaseModel):
    policy_name: str
    document_ref: str = ""
    inception_date: date
    annual_aggregate_limit: float
    benefits: dict[str, BenefitRule]
    endorsements: list[Endorsement] = Field(default_factory=list)
    exclusions: ExclusionRules = Field(default_factory=ExclusionRules)
    pre_auth_rules: PreAuthRule = Field(default_factory=PreAuthRule)
    calculation_order: list[CalculationStep] = Field(
        default_factory=lambda: [
            CalculationStep.CAP_AT_ELIGIBLE,
            CalculationStep.SUBTRACT_DEDUCTIBLE,
            CalculationStep.APPLY_COINSURANCE,
            CalculationStep.INSURER_PAYS_REMAINDER,
        ]
    )

    def resolved_benefit(self, benefit_name: str) -> BenefitRule:
        """Return the BenefitRule for `benefit_name` with all applicable
        endorsements merged in. Endorsements always prevail over the base
        table per policy Section 5 / document preamble.
        """
        base = self.benefits[benefit_name]
        merged = base.model_copy(deep=True)
        for endt in self.endorsements:
            if endt.benefit != benefit_name:
                continue
            for field_name, value in endt.overrides.items():
                if field_name in ("in_network", "out_of_network") and isinstance(value, dict):
                    current = getattr(merged, field_name)
                    updated = current.model_copy(update=value)
                    setattr(merged, field_name, updated)
                else:
                    setattr(merged, field_name, value)
        return merged


# ---------------------------------------------------------------------------
# Settlement output
# ---------------------------------------------------------------------------


class Settlement(BaseModel):
    claim_id: str
    service_date: date
    benefit: str
    network: NetworkStatus
    billed_amount: float
    eligible_amount: float
    deductible: float
    coinsurance_pct: float
    coinsurance_amount: float
    pre_auth_penalty_amount: float = 0.0
    insurer_paid: float
    member_paid: float
    decision: str = Field(..., description="PAID, PARTIALLY_PAID, or EXCLUDED")
    reason: str
    benefit_sub_limit_remaining_after: Optional[float] = None
    aggregate_limit_remaining_after: Optional[float] = None

    @model_validator(mode="after")
    def _round_money_fields(self) -> "Settlement":
        for f in (
            "billed_amount",
            "eligible_amount",
            "deductible",
            "coinsurance_amount",
            "pre_auth_penalty_amount",
            "insurer_paid",
            "member_paid",
        ):
            setattr(self, f, round(getattr(self, f), 2))
        return self


class YearTotals(BaseModel):
    total_billed: float
    total_insurer_paid: float
    total_member_paid: float
    aggregate_limit: float
    aggregate_limit_remaining: float
