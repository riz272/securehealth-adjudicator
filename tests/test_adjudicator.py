import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from adjudicator import adjudicate_claims
from models import Claim, NetworkStatus, PolicyConfig, PreAuthStatus

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_config.json"


@pytest.fixture
def config() -> PolicyConfig:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return PolicyConfig(**json.load(f))


def _main_scenario_claims() -> list[Claim]:
    return [
        Claim(claim_id="C1", service_date="2025-02-15", benefit="Outpatient Consultation",
              network=NetworkStatus.IN_NETWORK, billed_amount=300, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="flu", is_chronic_related=False, is_elective=False),
        Claim(claim_id="C2", service_date="2025-03-10", benefit="Outpatient Consultation",
              network=NetworkStatus.IN_NETWORK, billed_amount=400, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="asthma review", is_chronic_related=True, is_elective=False),
        Claim(claim_id="C3", service_date="2025-08-05", benefit="Outpatient Consultation",
              network=NetworkStatus.IN_NETWORK, billed_amount=400, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="asthma review", is_chronic_related=True, is_elective=False),
        Claim(claim_id="C4", service_date="2025-09-12", benefit="Physiotherapy",
              network=NetworkStatus.IN_NETWORK, billed_amount=3000, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="back strain", is_chronic_related=False, is_elective=False),
        Claim(claim_id="C5", service_date="2025-10-03", benefit="Inpatient & Surgery",
              network=NetworkStatus.IN_NETWORK, billed_amount=18000, pre_auth_obtained=PreAuthStatus.NO,
              diagnosis_note="elective knee arthroscopy", is_chronic_related=False, is_elective=True),
        Claim(claim_id="C6", service_date="2025-11-20", benefit="Prescribed Medication",
              network=NetworkStatus.OUT_OF_NETWORK, billed_amount=500, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="pharmacy", is_chronic_related=False, is_elective=False),
    ]


def test_physiotherapy_endorsement_override(config):
    physio = config.resolved_benefit("Physiotherapy")
    assert physio.sub_limit == 4000
    assert physio.in_network.coinsurance_pct == 10


def test_aggregate_limit(config):
    assert config.annual_aggregate_limit == 250000


def test_c1_single_claim(config):
    settlements, _ = adjudicate_claims([_main_scenario_claims()[0]], config)
    s = settlements[0]
    assert s.insurer_paid == 225.0
    assert s.member_paid == 75.0
    assert s.decision == "PAID"


def test_full_main_scenario_totals(config):
    settlements, totals = adjudicate_claims(_main_scenario_claims(), config)
    by_id = {s.claim_id: s for s in settlements}

    assert by_id["C2"].decision == "EXCLUDED"
    assert by_id["C2"].insurer_paid == 0.0
    assert by_id["C2"].member_paid == 400.0

    assert by_id["C3"].insurer_paid == 315.0
    assert by_id["C3"].member_paid == 85.0

    assert by_id["C4"].insurer_paid == 2700.0
    assert by_id["C4"].member_paid == 300.0

    assert by_id["C5"].insurer_paid == 14400.0
    assert by_id["C5"].member_paid == 3600.0
    assert by_id["C5"].decision == "PARTIALLY_PAID"

    assert by_id["C6"].decision == "EXCLUDED"
    assert by_id["C6"].insurer_paid == 0.0
    assert by_id["C6"].member_paid == 500.0

    assert totals.total_insurer_paid == 17640.0
    assert totals.total_member_paid == 4960.0


def test_waiting_period_boundary_is_payable(config):
    claim = Claim(
        claim_id="X1", service_date="2025-07-01", benefit="Outpatient Consultation",
        network=NetworkStatus.IN_NETWORK, billed_amount=400, pre_auth_obtained=PreAuthStatus.NA,
        diagnosis_note="asthma review", is_chronic_related=True, is_elective=False,
    )
    settlements, _ = adjudicate_claims([claim], config)
    assert settlements[0].decision == "PAID"
    assert settlements[0].insurer_paid == 315.0


def test_benefit_sub_limit_caps_insurer_payment(config):
    claims = [
        Claim(claim_id="P1", service_date="2025-01-10", benefit="Physiotherapy",
              network=NetworkStatus.IN_NETWORK, billed_amount=20000, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="injury", is_chronic_related=False, is_elective=False),
        Claim(claim_id="P2", service_date="2025-02-10", benefit="Physiotherapy",
              network=NetworkStatus.IN_NETWORK, billed_amount=20000, pre_auth_obtained=PreAuthStatus.NA,
              diagnosis_note="injury", is_chronic_related=False, is_elective=False),
    ]
    settlements, totals = adjudicate_claims(claims, config)
    assert sum(s.insurer_paid for s in settlements) == 4000.0
    assert settlements[1].decision == "PARTIALLY_PAID"


def test_out_of_network_pharmacy_not_covered(config):
    claim = Claim(
        claim_id="Y1", service_date="2025-05-01", benefit="Prescribed Medication",
        network=NetworkStatus.OUT_OF_NETWORK, billed_amount=200, pre_auth_obtained=PreAuthStatus.NA,
        diagnosis_note="pharmacy", is_chronic_related=False, is_elective=False,
    )
    settlements, _ = adjudicate_claims([claim], config)
    assert settlements[0].decision == "EXCLUDED"
    assert settlements[0].insurer_paid == 0.0


def test_elective_surgery_with_preauth_no_penalty(config):
    claim = Claim(
        claim_id="Z1", service_date="2025-06-01", benefit="Inpatient & Surgery",
        network=NetworkStatus.IN_NETWORK, billed_amount=10000, pre_auth_obtained=PreAuthStatus.YES,
        diagnosis_note="elective surgery", is_chronic_related=False, is_elective=True,
    )
    settlements, _ = adjudicate_claims([claim], config)
    assert settlements[0].insurer_paid == 10000.0
    assert settlements[0].member_paid == 0.0
    assert settlements[0].decision == "PAID"
