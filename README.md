# SecureHealth Claim Adjudicator

Automated adjudication of health-insurance claims against the SecureHealth Plan B
policy wording. Given a policy document and a set of claims, the system decides,
per claim, what the insurer pays and what the member owes, with a fully auditable
derivation for every figure.

## Setup

```bash
pip install -r requirements.txt
```

## How to run

```bash
# Run adjudication against the bundled policy config and fixture claims (no LLM, no API key needed)
python src/main.py --no-llm

# Run the test suite
python -m pytest tests/ -v
```

Output is written to `outputs/settlement_statement.json` and `outputs/settlement_statement.md`. Q1-Q6 answers with derivations are in `answers/Q1_to_Q6.md`.

## Architecture

The pipeline has two stages that are kept separate.

**Extraction** (`src/extractor.py`) reads the policy and claims PDFs and maps them onto a fixed JSON schema using pdfplumber + an LLM. The LLM output is immediately validated by Pydantic - if a field is missing, the wrong type, or a benefit name doesn't match the schema, it fails loudly rather than passing bad data downstream. This is the only place an LLM is involved.

**Adjudication** (`src/adjudicator.py`) is plain deterministic Python. It takes a validated `PolicyConfig` and a list of `Claim` objects and applies the calculation order from the policy (cap at eligible amount, subtract deductible, apply coinsurance, insurer pays the remainder), with exclusion checks, coverage checks, and the pre-auth penalty applied before that. Results are clipped against the benefit sub-limit and annual aggregate limit.

All policy rules live in `config/policy_config.json` - benefit sub-limits, deductibles, coinsurance percentages, waiting period, pre-auth rules, and endorsements are all data, not code. Endorsement E1 is stored as an override entry and merged onto the base benefit at lookup time via `PolicyConfig.resolved_benefit()`, so the precedence rule is enforced in one place. Swapping in a different member or a different policy means changing the input data, not the code.

## Optional: LLM extraction

`src/extractor.py` implements extraction via a local Ollama model (OpenAI-compatible, no API key needed):

```bash
ollama pull qwen2.5:7b-instruct
python src/main.py --policy-pdf 02_SecureHealth_Policy_Wording.pdf --claims-pdf 03_Claim_Scenario_Main.pdf
```

This is left out of the primary path above because, on the development
machine used for this submission (16GB RAM, CPU-only, ~2-4GB free), no model
both fit in memory *and* extracted the documents correctly:

| Model | Size | Runs without freezing? | Extracts correctly? |
|---|---|---|---|
| `qwen2.5:7b-instruct` | 4.7GB | No (needs ~6GB+ free RAM) | Yes, when it ran |
| `qwen2.5:3b-instruct` | 1.9GB | Yes | No - dropped benefit entries, conflated sub-limit/deductible values |
| `qwen2.5-coder:1.5b-instruct` | 986MB | Yes | No - produced invalid JSON |

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

## Why RAG doesn't work here

A vector search retrieves chunks similar to a query. That breaks in a few specific ways on this task:

- **Rules live in different sections.** Computing the physiotherapy settlement needs the benefits table (Section 2), the endorsement (Section 5), the deductible/coinsurance definitions (Section 1), and the calculation order (Section 3). A similarity search on "physiotherapy" surfaces the benefits table but has no structural reason to also pull the endorsement or GC-1.

- **Override precedence isn't semantic.** Section 2 has far more text about physiotherapy than Endorsement E1's short override clause. A retriever ranking by similarity will likely surface Section 2 as more relevant and miss the endorsement that actually controls the answer.

- **Exclusions require date arithmetic.** Whether C2 is excluded depends on comparing the service date against inception date + 6 months. That's a calculation, not a text lookup.

- **The pre-auth penalty depends on an absent value.** C5 is penalised because `pre_auth_obtained = No`. RAG retrieves text that's present - it has no way to reason about a missing field in structured claim data.

The adjudicator avoids all of this by never asking an LLM to compute an answer. Rules are loaded from a versioned config, endorsements are merged by explicit precedence, and every exclusion and limit check is ordinary testable code.
