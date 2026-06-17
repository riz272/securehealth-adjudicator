"""LLM-assisted extraction of policy rules and claims from source PDFs.

Optional path - the only place an LLM is used. Output is validated against
the Pydantic models in models.py before it can reach the adjudicator.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

from models import Claim, PolicyConfig

load_dotenv()

_POLICY_EXTRACTION_SYSTEM_PROMPT = """You are a precise insurance policy data extractor.
You will be given the full text of a health insurance policy wording document.
Extract its rules into a JSON object that EXACTLY matches this schema (no extra keys, no missing keys):

{
  "policy_name": string,
  "document_ref": string,
  "inception_date": "YYYY-MM-DD",
  "annual_aggregate_limit": number,
  "benefits": {
    "<Benefit Name>": {
      "benefit": "<Benefit Name>",
      "sub_limit": number or null,
      "deductible_applies": boolean,
      "in_network": {"covered": boolean, "deductible": number, "coinsurance_pct": number},
      "out_of_network": {"covered": boolean, "deductible": number, "coinsurance_pct": number}
    }, ...
  },
  "endorsements": [
    {
      "endorsement_id": string,
      "benefit": "<Benefit Name being overridden>",
      "description": string,
      "overrides": { ...partial fields to override on the BenefitRule, e.g. "sub_limit": 4000, "in_network": {"coinsurance_pct": 10} }
    }
  ],
  "exclusions": {
    "chronic_waiting_period_months": integer,
    "general_exclusions": [string, ...]
  },
  "pre_auth_rules": {
    "benefits_requiring_preauth": [string, ...],
    "elective_only": boolean,
    "penalty_pct_if_missing": number
  },
  "calculation_order": ["cap_at_eligible_amount", "subtract_deductible", "apply_member_coinsurance", "insurer_pays_remainder"]
}

Rules:
- Read every section: Definitions, Table of Benefits, General Conditions, Exclusions, and any Endorsements.
- Endorsements OVERRIDE the base Table of Benefits values for the fields they mention. Represent them
  as separate override entries, NOT pre-merged into the base benefit - the calculation engine merges them.
- The inception_date and benefit-level deductible only apply to benefits where the policy text says a
  deductible applies (read General Conditions carefully).
- Only include a benefit in "benefits_requiring_preauth" if the policy text explicitly requires
  pre-authorisation for it.
- Output ONLY the JSON object, no commentary, no markdown fences.
"""

_CLAIMS_EXTRACTION_SYSTEM_PROMPT = """You are a precise insurance claims data extractor.
You will be given the full text of a claim scenario document (a member and a table of claim events),
plus the declared chronic/pre-existing condition for that member.
Extract the claims into a JSON array EXACTLY matching this schema per item (no extra keys, no missing keys):

[
  {
    "claim_id": string,
    "service_date": "YYYY-MM-DD",
    "benefit": "<Benefit Name, matching the policy's benefit names as closely as possible>",
    "network": "In-Network" or "Out-of-Network",
    "billed_amount": number,
    "pre_auth_obtained": "Yes" or "No" or "n/a",
    "diagnosis_note": string,
    "is_chronic_related": boolean,
    "is_elective": boolean
  }, ...
]

Rules:
- is_chronic_related = true only if the diagnosis/note ties the claim to the member's declared
  chronic/pre-existing condition (e.g. an asthma review when asthma is the declared condition).
  An unrelated acute illness is NOT chronic-related even if the member has a chronic condition.
- is_elective = true if the diagnosis/note indicates a non-emergency / elective procedure.
- pre_auth_obtained should reflect the table's "Pre-auth" column literally ("No" stays "No", "n/a" stays "n/a").
- Output ONLY the JSON array, no commentary, no markdown fences.
"""


def extract_pdf_text(pdf_path: str | Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n\n".join(page.extract_text() or "" for page in pdf.pages)


DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct")
DEFAULT_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")


def _call_llm(system_prompt: str, user_content: str, model: str = DEFAULT_MODEL) -> str:
    client = OpenAI(
        base_url=DEFAULT_BASE_URL,
        api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
    )
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"} if "object" in system_prompt[:40].lower() else None,
    )
    return response.choices[0].message.content


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def extract_policy_config(pdf_path: str | Path, model: str = DEFAULT_MODEL) -> PolicyConfig:
    text = extract_pdf_text(pdf_path)
    raw = _call_llm(_POLICY_EXTRACTION_SYSTEM_PROMPT, text, model=model)
    data = json.loads(_strip_code_fences(raw))
    return PolicyConfig(**data)


def extract_claims(pdf_path: str | Path, model: str = DEFAULT_MODEL) -> list[Claim]:
    text = extract_pdf_text(pdf_path)
    raw = _call_llm(_CLAIMS_EXTRACTION_SYSTEM_PROMPT, text, model=model)
    data = json.loads(_strip_code_fences(raw))
    return [Claim(**item) for item in data]


def save_policy_config(config: PolicyConfig, out_path: str | Path) -> None:
    Path(out_path).write_text(
        json.dumps(json.loads(config.model_dump_json()), indent=2), encoding="utf-8"
    )
