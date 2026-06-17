"""Orchestrator: runs the full extraction -> adjudication -> output pipeline.

Usage:
    python src/main.py                  # extract policy + claims via a local Ollama model (no API key needed)
    python src/main.py --no-llm          # use the bundled config/policy_config.json and a fixture claim
                                          # set, skipping any LLM calls entirely (useful for grading/CI)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tabulate import tabulate

sys.path.insert(0, str(Path(__file__).parent))

from adjudicator import adjudicate_claims  # noqa: E402
from models import Claim, NetworkStatus, PolicyConfig, PreAuthStatus  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT  # PDFs are expected alongside this project unless overridden
CONFIG_PATH = REPO_ROOT / "config" / "policy_config.json"
OUTPUTS_DIR = REPO_ROOT / "outputs"


def load_policy_config(path: Path = CONFIG_PATH) -> PolicyConfig:
    with open(path, encoding="utf-8") as f:
        return PolicyConfig(**json.load(f))


def fixture_claims() -> list[Claim]:
    """The six claims from 03_Claim_Scenario_Main.pdf."""
    return [
        Claim(
            claim_id="C1", service_date="2025-02-15", benefit="Outpatient Consultation",
            network=NetworkStatus.IN_NETWORK, billed_amount=300, pre_auth_obtained=PreAuthStatus.NA,
            diagnosis_note="Acute viral illness (influenza) - unrelated to asthma",
            is_chronic_related=False, is_elective=False,
        ),
        Claim(
            claim_id="C2", service_date="2025-03-10", benefit="Outpatient Consultation",
            network=NetworkStatus.IN_NETWORK, billed_amount=400, pre_auth_obtained=PreAuthStatus.NA,
            diagnosis_note="Asthma review (declared chronic condition)",
            is_chronic_related=True, is_elective=False,
        ),
        Claim(
            claim_id="C3", service_date="2025-08-05", benefit="Outpatient Consultation",
            network=NetworkStatus.IN_NETWORK, billed_amount=400, pre_auth_obtained=PreAuthStatus.NA,
            diagnosis_note="Asthma review (declared chronic condition)",
            is_chronic_related=True, is_elective=False,
        ),
        Claim(
            claim_id="C4", service_date="2025-09-12", benefit="Physiotherapy",
            network=NetworkStatus.IN_NETWORK, billed_amount=3000, pre_auth_obtained=PreAuthStatus.NA,
            diagnosis_note="Lower-back strain (acute)",
            is_chronic_related=False, is_elective=False,
        ),
        Claim(
            claim_id="C5", service_date="2025-10-03", benefit="Inpatient & Surgery",
            network=NetworkStatus.IN_NETWORK, billed_amount=18000, pre_auth_obtained=PreAuthStatus.NO,
            diagnosis_note="Elective knee arthroscopy (non-emergency)",
            is_chronic_related=False, is_elective=True,
        ),
        Claim(
            claim_id="C6", service_date="2025-11-20", benefit="Prescribed Medication",
            network=NetworkStatus.OUT_OF_NETWORK, billed_amount=500, pre_auth_obtained=PreAuthStatus.NA,
            diagnosis_note="Pharmacy purchase at non-network pharmacy",
            is_chronic_related=False, is_elective=False,
        ),
    ]


def build_settlement_table(settlements, totals) -> str:
    rows = []
    for s in settlements:
        rows.append([
            s.claim_id,
            s.service_date.isoformat(),
            s.benefit,
            s.network.value,
            f"{s.billed_amount:,.2f}",
            f"{s.eligible_amount:,.2f}",
            f"{s.deductible:,.2f}",
            f"{s.coinsurance_pct:.0f}%",
            f"{s.insurer_paid:,.2f}",
            f"{s.member_paid:,.2f}",
            s.decision,
        ])
    headers = [
        "Claim", "Service Date", "Benefit", "Network", "Billed", "Eligible",
        "Deductible", "Coins. %", "Insurer Paid", "Member Paid", "Decision",
    ]
    table = tabulate(rows, headers=headers, tablefmt="github")

    totals_block = (
        f"\n\n**Year Totals**\n\n"
        f"- Total billed: AED {totals.total_billed:,.2f}\n"
        f"- Total insurer paid: AED {totals.total_insurer_paid:,.2f}\n"
        f"- Total member out-of-pocket: AED {totals.total_member_paid:,.2f}\n"
        f"- Annual aggregate limit: AED {totals.aggregate_limit:,.2f}\n"
        f"- Aggregate limit remaining: AED {totals.aggregate_limit_remaining:,.2f}\n"
    )
    reasons_block = "\n\n**Decision reasons**\n\n" + "\n".join(
        f"- **{s.claim_id}**: {s.reason}" for s in settlements
    )
    return table + totals_block + reasons_block


def build_settlement_json(settlements, totals) -> dict:
    return {
        "claims": [json.loads(s.model_dump_json()) for s in settlements],
        "year_totals": json.loads(totals.model_dump_json()),
    }


def print_q1_to_q6(config: PolicyConfig, settlements, totals) -> None:
    physio = config.resolved_benefit("Physiotherapy")
    c1 = next(s for s in settlements if s.claim_id == "C1")

    print("=" * 70)
    print("Q1 - Physiotherapy coinsurance % and annual sub-limit")
    print("=" * 70)
    print(f"  In-network coinsurance: {physio.in_network.coinsurance_pct:.0f}% (after Endorsement E1 override)")
    print(f"  Annual sub-limit: AED {physio.sub_limit:,.2f} (after Endorsement E1 override)")

    print("\n" + "=" * 70)
    print("Q2 - Annual Aggregate Limit")
    print("=" * 70)
    print(f"  AED {config.annual_aggregate_limit:,.2f}")

    print("\n" + "=" * 70)
    print("Q3 - Claim C1: insurer pays vs member out-of-pocket")
    print("=" * 70)
    print(f"  Eligible amount: AED {c1.eligible_amount:,.2f}")
    print(f"  Deductible: AED {c1.deductible:,.2f}")
    print(f"  Remainder: AED {c1.eligible_amount - c1.deductible:,.2f}")
    print(f"  Member coinsurance ({c1.coinsurance_pct:.0f}%): AED {c1.coinsurance_amount:,.2f}")
    print(f"  Insurer pays: AED {c1.insurer_paid:,.2f}")
    print(f"  Member out-of-pocket: AED {c1.member_paid:,.2f}")

    print("\n" + "=" * 70)
    print("Q4 - Claims not payable in full or in part")
    print("=" * 70)
    for s in settlements:
        if s.decision != "PAID":
            print(f"  {s.claim_id} ({s.decision}): {s.reason}")

    print("\n" + "=" * 70)
    print("Q5 - Year totals")
    print("=" * 70)
    print(f"  Total insurer paid: AED {totals.total_insurer_paid:,.2f}")
    print(f"  Total member out-of-pocket: AED {totals.total_member_paid:,.2f}")

    print("\n" + "=" * 70)
    print("Q6 - Settlement statement written to outputs/")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="SecureHealth claim adjudication pipeline")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM extraction; use bundled config/policy_config.json and fixture claims.",
    )
    parser.add_argument(
        "--policy-pdf", default=str(REPO_ROOT / "02_SecureHealth_Policy_Wording.pdf"),
        help="Path to the policy wording PDF (only used without --no-llm).",
    )
    parser.add_argument(
        "--claims-pdf", default=str(REPO_ROOT / "03_Claim_Scenario_Main.pdf"),
        help="Path to the claim scenario PDF (only used without --no-llm).",
    )
    args = parser.parse_args()

    if args.no_llm:
        config = load_policy_config()
        claims = fixture_claims()
    else:
        from extractor import extract_claims, extract_policy_config, save_policy_config

        policy_pdf = Path(args.policy_pdf)
        claims_pdf = Path(args.claims_pdf)

        if policy_pdf.exists():
            config = extract_policy_config(policy_pdf)
            save_policy_config(config, CONFIG_PATH)
        else:
            print(f"[warn] {policy_pdf} not found; falling back to bundled config/policy_config.json")
            config = load_policy_config()

        if claims_pdf.exists():
            claims = extract_claims(claims_pdf)
        else:
            print(f"[warn] {claims_pdf} not found; falling back to fixture claims")
            claims = fixture_claims()

    settlements, totals = adjudicate_claims(claims, config)

    print_q1_to_q6(config, settlements, totals)

    OUTPUTS_DIR.mkdir(exist_ok=True)
    json_path = OUTPUTS_DIR / "settlement_statement.json"
    md_path = OUTPUTS_DIR / "settlement_statement.md"

    json_path.write_text(
        json.dumps(build_settlement_json(settlements, totals), indent=2), encoding="utf-8"
    )
    md_path.write_text(
        "# Settlement Statement\n\n" + build_settlement_table(settlements, totals), encoding="utf-8"
    )

    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
