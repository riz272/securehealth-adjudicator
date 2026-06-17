"""Deterministic adjudication engine. No LLM calls, no PDF/file I/O - takes a
validated PolicyConfig and Claim list and produces Settlement objects per
GC-1, in claim-date order, tracking running totals against sub-limits and
the annual aggregate limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from dateutil.relativedelta import relativedelta

from models import Claim, NetworkStatus, PolicyConfig, Settlement, YearTotals


@dataclass
class _RunningTotals:
    aggregate_paid: float = 0.0
    sub_limit_paid: dict[str, float] = field(default_factory=dict)

    def remaining_aggregate(self, limit: float) -> float:
        return limit - self.aggregate_paid

    def remaining_sub_limit(self, benefit: str, limit: float | None) -> float | None:
        if limit is None:
            return None
        return limit - self.sub_limit_paid.get(benefit, 0.0)


def _is_within_waiting_period(service_date: date, inception_date: date, months: int) -> bool:
    waiting_period_end = inception_date + relativedelta(months=months)
    return service_date < waiting_period_end


def adjudicate_claim(
    claim: Claim,
    config: PolicyConfig,
    totals: _RunningTotals,
) -> Settlement:
    benefit_rule = config.resolved_benefit(claim.benefit)
    terms = (
        benefit_rule.in_network
        if claim.network == NetworkStatus.IN_NETWORK
        else benefit_rule.out_of_network
    )

    if claim.is_chronic_related and _is_within_waiting_period(
        claim.service_date, config.inception_date, config.exclusions.chronic_waiting_period_months
    ):
        waiting_end = config.inception_date + relativedelta(
            months=config.exclusions.chronic_waiting_period_months
        )
        return Settlement(
            claim_id=claim.claim_id,
            service_date=claim.service_date,
            benefit=claim.benefit,
            network=claim.network,
            billed_amount=claim.billed_amount,
            eligible_amount=0.0,
            deductible=0.0,
            coinsurance_pct=terms.coinsurance_pct,
            coinsurance_amount=0.0,
            insurer_paid=0.0,
            member_paid=claim.billed_amount,
            decision="EXCLUDED",
            reason=(
                f"Exclusion 4.2 - chronic/pre-existing condition waiting period. "
                f"Service date {claim.service_date.isoformat()} is before the waiting "
                f"period ends on {waiting_end.isoformat()} "
                f"({config.exclusions.chronic_waiting_period_months} months from inception "
                f"{config.inception_date.isoformat()}). Member is liable for the full billed amount."
            ),
            benefit_sub_limit_remaining_after=totals.remaining_sub_limit(
                claim.benefit, benefit_rule.sub_limit
            ),
            aggregate_limit_remaining_after=totals.remaining_aggregate(config.annual_aggregate_limit),
        )

    if not terms.covered:
        return Settlement(
            claim_id=claim.claim_id,
            service_date=claim.service_date,
            benefit=claim.benefit,
            network=claim.network,
            billed_amount=claim.billed_amount,
            eligible_amount=0.0,
            deductible=0.0,
            coinsurance_pct=0.0,
            coinsurance_amount=0.0,
            insurer_paid=0.0,
            member_paid=claim.billed_amount,
            decision="EXCLUDED",
            reason=(
                f"Not covered: {claim.benefit} ({claim.network.value}) is excluded from cover "
                f"under the Table of Benefits. Member is liable for the full billed amount."
            ),
            benefit_sub_limit_remaining_after=totals.remaining_sub_limit(
                claim.benefit, benefit_rule.sub_limit
            ),
            aggregate_limit_remaining_after=totals.remaining_aggregate(config.annual_aggregate_limit),
        )

    preauth_required = (
        claim.benefit in config.pre_auth_rules.benefits_requiring_preauth
        and (not config.pre_auth_rules.elective_only or claim.is_elective)
    )
    preauth_penalty_pct = 0.0
    preauth_note = ""
    if preauth_required and claim.pre_auth_obtained.value != "Yes":
        preauth_penalty_pct = config.pre_auth_rules.penalty_pct_if_missing
        preauth_note = (
            f" GC-3 pre-authorisation was required but not obtained; insurer's payable "
            f"amount is reduced by {preauth_penalty_pct:.0f}%, borne by the member."
        )

    eligible_amount = round(claim.billed_amount, 2)
    deductible = terms.deductible if benefit_rule.deductible_applies else 0.0
    deductible = min(deductible, eligible_amount)
    remainder_after_deductible = eligible_amount - deductible
    coinsurance_amount = round(remainder_after_deductible * (terms.coinsurance_pct / 100), 2)
    insurer_payable_before_penalty = round(remainder_after_deductible - coinsurance_amount, 2)

    preauth_penalty_amount = round(insurer_payable_before_penalty * (preauth_penalty_pct / 100), 2)
    insurer_payable = round(insurer_payable_before_penalty - preauth_penalty_amount, 2)
    member_payable = round(deductible + coinsurance_amount + preauth_penalty_amount, 2)

    remaining_sub_limit = totals.remaining_sub_limit(claim.benefit, benefit_rule.sub_limit)
    remaining_aggregate = totals.remaining_aggregate(config.annual_aggregate_limit)

    capped_reasons = []
    final_insurer_paid = insurer_payable

    if remaining_sub_limit is not None and final_insurer_paid > remaining_sub_limit:
        capped_reasons.append(
            f"capped at remaining {claim.benefit} sub-limit of AED {remaining_sub_limit:.2f} (GC-2)"
        )
        final_insurer_paid = max(remaining_sub_limit, 0.0)

    if final_insurer_paid > remaining_aggregate:
        capped_reasons.append(
            f"capped at remaining annual aggregate limit of AED {remaining_aggregate:.2f} (GC-2)"
        )
        final_insurer_paid = max(remaining_aggregate, 0.0)

    final_insurer_paid = round(final_insurer_paid, 2)
    shortfall_from_limit = round(insurer_payable - final_insurer_paid, 2)
    final_member_paid = round(member_payable + shortfall_from_limit, 2)

    decision = "PAID"
    reason_parts = [
        f"GC-1: eligible amount AED {eligible_amount:.2f} (within R&C); "
        f"deductible AED {deductible:.2f}; "
        f"member coinsurance {terms.coinsurance_pct:.0f}% of AED {remainder_after_deductible:.2f} "
        f"= AED {coinsurance_amount:.2f}; insurer pays remainder."
    ]
    if preauth_note:
        reason_parts.append(preauth_note.strip())
        decision = "PARTIALLY_PAID"
    if capped_reasons:
        reason_parts.append("; ".join(capped_reasons) + ".")
        decision = "PARTIALLY_PAID"

    settlement = Settlement(
        claim_id=claim.claim_id,
        service_date=claim.service_date,
        benefit=claim.benefit,
        network=claim.network,
        billed_amount=claim.billed_amount,
        eligible_amount=eligible_amount,
        deductible=deductible,
        coinsurance_pct=terms.coinsurance_pct,
        coinsurance_amount=coinsurance_amount,
        pre_auth_penalty_amount=preauth_penalty_amount,
        insurer_paid=final_insurer_paid,
        member_paid=final_member_paid,
        decision=decision,
        reason=" ".join(reason_parts),
        benefit_sub_limit_remaining_after=None,
        aggregate_limit_remaining_after=None,
    )

    totals.aggregate_paid += final_insurer_paid
    totals.sub_limit_paid[claim.benefit] = totals.sub_limit_paid.get(claim.benefit, 0.0) + final_insurer_paid

    settlement.benefit_sub_limit_remaining_after = totals.remaining_sub_limit(
        claim.benefit, benefit_rule.sub_limit
    )
    settlement.aggregate_limit_remaining_after = totals.remaining_aggregate(config.annual_aggregate_limit)

    return settlement


def adjudicate_claims(claims: list[Claim], config: PolicyConfig) -> tuple[list[Settlement], YearTotals]:
    totals = _RunningTotals()
    ordered = sorted(claims, key=lambda c: c.service_date)

    settlements: list[Settlement] = []
    for claim in ordered:
        settlements.append(adjudicate_claim(claim, config, totals))

    year_totals = YearTotals(
        total_billed=round(sum(s.billed_amount for s in settlements), 2),
        total_insurer_paid=round(sum(s.insurer_paid for s in settlements), 2),
        total_member_paid=round(sum(s.member_paid for s in settlements), 2),
        aggregate_limit=config.annual_aggregate_limit,
        aggregate_limit_remaining=round(totals.remaining_aggregate(config.annual_aggregate_limit), 2),
    )

    return settlements, year_totals
