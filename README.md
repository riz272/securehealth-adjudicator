# SecureHealth Claim Adjudicator

Automated adjudication of health-insurance claims against the SecureHealth Plan B
policy wording. Given a policy document and a set of claims, the system decides,
per claim, what the insurer pays and what the member owes, with a fully auditable
derivation for every figure.

## Setup

```bash
pip install -r requirements.txt
```

No API key or model download is required to run the verified pipeline (see
below). LLM-based extraction from raw PDFs is implemented but optional, see
[Optional: LLM-based extraction](#optional-llm-based-extraction-experimental).

## How to run

```bash
# The verified path: bundled config/policy_config.json (the rules-as-data
# representation of 02_SecureHealth_Policy_Wording.pdf) + the six claims from
# 03_Claim_Scenario_Main.pdf as a fixture. No LLM calls. This is what produces
# the Q1-Q6 answers in answers/Q1_to_Q6.md.
python src/main.py --no-llm
```

```bash
python -m pytest tests/ -v
```

This writes `outputs/settlement_statement.json` and `outputs/settlement_statement.md`,
and prints answers to Q1–Q6 to stdout.

## Architecture

The pipeline is split into two halves that never share responsibilities, so
that the extraction step (below) can be swapped or skipped without touching
adjudication at all. Extraction (`src/extractor.py`) is the only place an LLM
would be used: it would pull text out of the PDFs with `pdfplumber` and
prompt an LLM to map that text onto a fixed JSON schema. The LLM's raw output
is immediately parsed and passed through Pydantic models (`src/models.py`).
If it doesn't fit the schema (wrong types, missing fields, an invented benefit
name), construction fails loudly rather than letting bad data reach the
calculation engine. Adjudication (`src/adjudicator.py`) is pure,
deterministic Python: no LLM calls, no file I/O, no knowledge of PDFs. It
takes a validated `PolicyConfig` and a list of validated `Claim` objects and
applies General Condition 1's order of operations (cap at eligible amount,
subtract deductible, apply coinsurance, insurer pays the remainder), gated
by exclusion checks, coverage checks, and the pre-authorisation penalty, then
clips the result against the remaining benefit sub-limit and annual aggregate
limit.

All policy rules live in `config/policy_config.json` as data, not code:
benefit sub-limits, deductibles, coinsurance percentages, the waiting period,
pre-auth rules, and endorsements are all fields in that file. Endorsement E1
is represented as an *override* entry (`benefit: "Physiotherapy"`, with the
fields it changes), not pre-merged into the base table; `PolicyConfig.resolved_benefit()`
applies overrides on top of the base benefit at lookup time, in the order
endorsements are listed, so the precedence rule ("Endorsement prevails") is
enforced once, in one place, rather than re-derived by hand for each benefit.
Changing a number in the JSON (lowering the aggregate limit, or removing the
endorsement) changes the output with no code changes, and a second member
with different dates, providers, and amounts runs through exactly the same
`adjudicate_claims()` function with no claim-specific logic anywhere.

`src/main.py` orchestrates the two halves and renders results both as
machine-readable JSON (`outputs/settlement_statement.json`) and a human-readable
Markdown table (`outputs/settlement_statement.md`), each settlement carrying its
own `reason` string that cites the specific clause (GC-1, GC-3, Exclusion 4.2,
Endorsement E1) that produced it.

## Optional: LLM-based extraction (experimental)

The brief permits but does not require an LLM ("You may use any libraries,
models or tools. If you use an LLM, we care how you constrain and verify it").
`src/extractor.py` implements the LLM path against a local model via
[Ollama](https://ollama.com) (OpenAI-compatible API, no key needed):

```bash
ollama pull qwen2.5:7b-instruct
python src/main.py --policy-pdf 02_SecureHealth_Policy_Wording.pdf --claims-pdf 03_Claim_Scenario_Main.pdf
```

This is left out of the primary path above because, on the development
machine used for this submission (16GB RAM, CPU-only, ~2-4GB free), no model
both fit in memory *and* extracted the documents correctly:

| Model | Size | Runs without freezing? | Extracts correctly? |
|---|---|---|---|
| `qwen2.5:7b-instruct` | 4.7GB | No (needs ~6GB+ free RAM) | Yes, when it could run |
| `qwen2.5:3b-instruct` | 1.9GB | Yes | No — dropped two benefit entries, conflated sub-limit/deductible values, once confused the claims table's Network column for the Benefit column |
| `qwen2.5-coder:1.5b-instruct` | 986MB | Yes | No — produced syntactically invalid JSON |

In every failure case, the Pydantic validation layer caught the bad output
immediately: a `ValidationError` on out-of-range/missing fields, a `KeyError`
when the adjudicator looked up a benefit that extraction had silently renamed,
or a `JSONDecodeError` on malformed output, rather than letting a wrong
number reach a settlement. None of these produced an incorrect answer
silently, which is the property the brief asks for ("if you use an LLM, we
care how you constrain and verify it"). On a machine with more headroom,
`qwen2.5:7b-instruct` is expected to extract both documents correctly and is
the recommended starting point. Swap models via the `LLM_MODEL` env var (see
`.env.example`) with no code changes, or point `LLM_BASE_URL`/`OPENAI_API_KEY`
at a hosted provider instead.

## Why naive RAG breaks on this task

A vector-search/RAG approach retrieves chunks that are semantically similar
to a query, then asks an LLM to answer from those chunks. That model fails here
in several specific ways:

- The answer requires combining non-co-located rules. Computing what's
  payable for the physiotherapy claim requires the Table of Benefits (Section 2),
  the Endorsement (Section 5), the Definitions of Deductible/Coinsurance
  (Section 1), and General Condition 1's order of operations (Section 3),
  four different sections written in different parts of the document. A
  similarity search over "physiotherapy" will surface the benefits table
  but has no structural reason to also retrieve GC-1 or the endorsement
  unless the right keywords happen to co-occur.

- Override precedence is invisible to retrieval. Section 2 says
  "Physiotherapy: 20% coinsurance, AED 2,500 sub-limit" in a dense table,
  far more text and more direct keyword matches than Endorsement E1's
  shorter override clause. A retriever ranking by similarity is likely to
  surface the base table as the "more relevant" chunk and never surface, or
  under-weight, the endorsement that actually controls the answer. The
  policy is explicit that "Where an Endorsement conflicts... the Endorsement
  prevails", but that's a structural/legal rule, not a semantic one, and
  nothing in embedding space encodes it.

- The waiting-period exclusion requires date arithmetic, not text similarity.
  Whether claim C2 is excluded depends on comparing `2025-03-10` against
  `inception_date + 6 months`. No amount of retrieving the right paragraph
  answers that question; it requires executing a calculation, which an LLM
  asked to "answer from these chunks" will sometimes get right and sometimes
  get wrong, with no way to audit which.

- The pre-authorisation penalty depends on the absence of a field.
  C5 is penalized because `pre_auth_obtained = No` for an elective procedure.
  RAG retrieves text that's present; it has no mechanism for reasoning about
  a missing fact in structured claim data, because the claim data isn't even
  the kind of thing RAG retrieves from, it's tabular, not prose.

This design avoids all four failure modes by never asking an LLM to compute
an answer, whether or not LLM-based extraction is used at all. The rules
either come from `config/policy_config.json` directly (the verified path) or,
optionally, from one-time LLM extraction into that same fixed, versioned
schema (`PolicyConfig`/`Claim`), validated by Pydantic immediately on output.
Either way, every override, exclusion, limit, and date rule is evaluated by
ordinary, testable, deterministic code (`adjudicator.py`) that has no notion
of semantic similarity. It looks up `config.resolved_benefit("Physiotherapy")`,
which merges the endorsement onto the base rule by explicit precedence (last
endorsement wins, applied after the base), the same way every time, regardless
of how the policy document happens to phrase things.
