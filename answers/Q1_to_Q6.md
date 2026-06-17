# Answers Q1-Q6

All figures below were produced by `python src/main.py --no-llm`, which runs
the deterministic adjudication engine (`src/adjudicator.py`) against
`config/policy_config.json` and the six claims in `03_Claim_Scenario_Main.pdf`.
Nothing here is hardcoded to this member; the same code path runs for any
policy config and claim list (see `tests/test_adjudicator.py` for a second,
unseen claim scenario that exercises sub-limit capping).

## Q1 - Physiotherapy: coinsurance % and annual sub-limit

Two sections are relevant and they conflict:

- Section 2 (Table of Benefits): Physiotherapy, sub-limit AED 2,500,
  member coinsurance 20% in-network, 30% out-of-network.
- Section 5 (Endorsement E1, Physiotherapy Enhancement), effective from the
  Inception Date: "Notwithstanding the Table of Benefits... (i) the Member
  coinsurance is reduced to 10% for In-Network treatment; and (ii) the
  annual sub-limit is increased to AED 4,000... This Endorsement prevails
  over Section 2."

Per the document preamble ("Where an Endorsement conflicts with the Table of
Benefits... the Endorsement prevails") and the explicit text of E1 itself, the
Endorsement wins.

Final applicable terms:
- In-network coinsurance: 10%
- Annual sub-limit: AED 4,000
- Out-of-network coinsurance is unaffected by E1, so it remains 30% per
  Section 2; E1 only addresses In-Network treatment.

## Q2 - Annual Aggregate Limit

AED 250,000. Section 2: "Annual Aggregate Limit: AED 250,000 - the
maximum the Insurer will pay across all benefits in a Policy Year."

## Q3 - Claim C1 (Outpatient Consultation, In-Network, AED 300, 15 Feb 2025, acute viral illness unrelated to asthma)

Step-by-step per GC-1 ("in this order: (a) cap the billed amount at the
Eligible Amount; (b) subtract any Deductible; (c) apply the Member coinsurance
percentage to the remainder; (d) the Insurer pays the balance"):

| Step | Calculation | Result |
|---|---|---|
| Chronic waiting period? | Diagnosis is "unrelated to asthma", not chronic-related, Exclusion 4.2 does not apply | N/A |
| (a) Eligible amount | Billed AED 300, within R&C | AED 300.00 |
| (b) Deductible | Outpatient Consultation, In-Network, AED 50 per visit (Section 2 / GC-4) | AED 50.00 |
| Remainder | 300.00 minus 50.00 | AED 250.00 |
| (c) Member coinsurance | In-Network Outpatient Consultation = 10% of remainder | 10% x 250.00 = AED 25.00 |
| (d) Insurer pays | Remainder minus member coinsurance | 250.00 minus 25.00 = AED 225.00 |
| Member out-of-pocket | Deductible plus coinsurance | 50.00 plus 25.00 = AED 75.00 |

Insurer pays: AED 225.00. Member out-of-pocket: AED 75.00.

## Q4 - Claims not payable in full or in part

| Claim | Status | Clause | Reason |
|---|---|---|---|
| C2 (10 Mar 2025, Outpatient, asthma review, AED 400) | Excluded in full | Exclusion 4.2 (Chronic/Pre-existing waiting period) | Asthma is the member's declared chronic/pre-existing condition. The 6-month waiting period runs from the Inception Date (1 Jan 2025) to 1 Jul 2025. Service date 10 Mar 2025 falls inside that window, so the claim is not payable. Member pays the full AED 400. |
| C5 (3 Oct 2025, Inpatient & Surgery, elective knee arthroscopy, AED 18,000, no pre-auth) | Partially paid, reduced by 20% | GC-3 (Pre-authorisation) | Elective Inpatient & Surgery requires pre-authorisation at least 48 hours before admission. None was obtained ("Pre-auth: No"), and this was non-emergency. GC-3: "the Insurer reduces the amount otherwise payable for that treatment by 20%; the Member bears the reduction." |
| C6 (20 Nov 2025, Prescribed Medication, Out-of-Network, AED 500) | Excluded in full | Section 2, Table of Benefits | Prescribed Medication out-of-network member share is "Not covered." Member pays the full AED 500. |

All other claims (C1, C3, C4) are payable in full under the standard GC-1
calculation. C3 is the asthma review on 5 Aug 2025, which is after the
waiting period ends (1 Jul 2025), so Exclusion 4.2 no longer applies to it.

## Q5 - Full-year totals across all six claims

Claims are processed in service-date order, with insurer payments accumulated
against each benefit's sub-limit and the annual aggregate limit (GC-2). None
of the six claims approach any sub-limit or the AED 250,000 aggregate, so no
additional capping occurs beyond the C5 pre-auth penalty.

| Claim | Benefit | Billed | Eligible | Deductible | Coins. % | Insurer Paid | Member Paid | Decision |
|---|---|---|---|---|---|---|---|---|
| C1 | Outpatient Consultation | 300.00 | 300.00 | 50.00 | 10% | 225.00 | 75.00 | PAID |
| C2 | Outpatient Consultation | 400.00 | 0.00 | 0.00 | n/a | 0.00 | 400.00 | EXCLUDED |
| C3 | Outpatient Consultation | 400.00 | 400.00 | 50.00 | 10% | 315.00 | 85.00 | PAID |
| C4 | Physiotherapy | 3,000.00 | 3,000.00 | 0.00 | 10% | 2,700.00 | 300.00 | PAID |
| C5 | Inpatient & Surgery | 18,000.00 | 18,000.00 | 0.00 | 0% (+20% pre-auth penalty) | 14,400.00 | 3,600.00 | PARTIALLY_PAID |
| C6 | Prescribed Medication | 500.00 | 0.00 | 0.00 | n/a | 0.00 | 500.00 | EXCLUDED |

Derivations for C3, C4, C5 (C1/C2/C6 shown above and in Q4):

- C3: Eligible 400.00, deductible 50.00, remainder 350.00, 10%
  coinsurance = 35.00, insurer pays 350.00 minus 35.00 = 315.00; member pays
  50.00 plus 35.00 = 85.00.
- C4: Physiotherapy, In-Network, E1 applies (10% coinsurance, no
  deductible on this benefit). Eligible 3,000.00, remainder 3,000.00, 10%
  coinsurance = 300.00, insurer pays 2,700.00; member pays 300.00.
  Cumulative physiotherapy paid (2,700.00) stays under the E1 sub-limit of
  4,000.00.
- C5: Inpatient & Surgery, In-Network, 0% coinsurance, full cover before
  penalty: insurer would pay 18,000.00. No pre-auth, so GC-3's 20% penalty on
  the insurer's share applies: 18,000.00 x 20% = 3,600.00 reduction. Insurer
  pays 18,000.00 minus 3,600.00 = 14,400.00; member pays the penalty amount,
  3,600.00.

Total amount payable by the insurer: AED 225.00 + 0.00 + 315.00 + 2,700.00 + 14,400.00 + 0.00 = AED 17,640.00

Member's total out-of-pocket: AED 75.00 + 400.00 + 85.00 + 300.00 + 3,600.00 + 500.00 = AED 4,960.00

Aggregate limit check: AED 17,640.00 is well within the AED 250,000.00 annual
aggregate limit. No aggregate-level capping applies this year.

## Q6 - Structured settlement statement

Delivered as both machine-readable JSON and a human-readable table, generated
by `src/main.py` into:

- [`outputs/settlement_statement.json`](../outputs/settlement_statement.json)
- [`outputs/settlement_statement.md`](../outputs/settlement_statement.md)

Each row carries `billed_amount`, `eligible_amount`, `deductible`,
`coinsurance_pct` / `coinsurance_amount`, `insurer_paid`, `member_paid`,
`decision` (`PAID` / `PARTIALLY_PAID` / `EXCLUDED`), and a `reason` string
citing the specific clause applied, plus year totals
(`total_insurer_paid`, `total_member_paid`, `aggregate_limit_remaining`).
