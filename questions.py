import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
SOURCE_REPO = "Near-One/btc-light-client-contract"
REPO_NAME = "btc-light-client-contract"
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    "btc-types/src/aux.rs",
    "btc-types/src/btc_header.rs",
    "btc-types/src/contract_args.rs",
    "btc-types/src/hash.rs",
    "btc-types/src/header.rs",
    "btc-types/src/lib.rs",
    "btc-types/src/network.rs",
    "btc-types/src/u256.rs",
    "btc-types/src/utils.rs",
    "btc-types/src/zcash_header.rs",
    "contract/src/bitcoin.rs",
    "contract/src/dogecoin.rs",
    "contract/src/lib.rs",
    "contract/src/litecoin.rs",
    "contract/src/utils.rs",
    "contract/src/zcash.rs",
    "merkle-tools/src/lib.rs",
    "relayer/src/adaptive_batch.rs",
    "relayer/src/bitcoin_client.rs",
    "relayer/src/config.rs",
    "relayer/src/lib.rs",
    "relayer/src/main.rs",
    "relayer/src/near_client.rs",
]

target_scopes = [
    "Critical. Acceptance of an invalid, malformed, under-work, cross-network, or incorrectly retargeted block header or chain segment as canonical mainchain state, enabling false confirmations or downstream asset release.",
    "Critical. Fork-choice, reorg, chainwork, or canonical-mapping corruption that promotes a lower-work fork, rewrites the wrong heights, or desynchronizes tip, hash, and height state used for verification.",
    "Critical. Merkle proof or transaction inclusion validation flaw that returns true for a nonexistent transaction, wrong index, wrong block, forged internal node, or otherwise invalid inclusion claim.",
    "Critical. Initialization, migration, or genesis-bootstrap flaw that lets an attacker install unsafe starting state, bypass required private or init constraints, or preserve corrupted storage so invalid chain history becomes trusted.",
    "Critical. Authorization or role-bypass in NEAR contract entrypoints that enables unauthorized block submission, pause bypass, garbage-collection control, upgrade, migration, init control, or other trusted-relayer or DAO protected actions.",
    "Critical. Garbage-collection, confirmation-window, or historical-header retention bug that deletes, rewrites, or misclassifies canonical history in a way that causes false verification, permanent proof lockout, or chain-state corruption.",
    "Critical. Arithmetic, endianness, serialization, hashing, or U256, target, or work conversion flaw that miscomputes proof-of-work validity, chainwork, header identity, difficulty, or merkle roots.",
    "Critical. Dogecoin AuxPoW validation flaw that accepts an invalid parent block, chain merkle branch, coinbase commitment, chain ID, or expected index, causing false canonical header acceptance.",
    "Critical. Zcash, Bitcoin, or Litecoin consensus-rule enforcement bug, including Equihash, timestamp windows, difficulty adjustment, min-difficulty handling, or version gating, that admits headers invalid under the intended network rules.",
    "Critical. Cross-module desynchronization between btc-types, merkle-tools, contract state, and relayer transaction assembly, nonce, or batch logic that causes replayed, skipped, mis-ordered, or incorrectly trusted canonical updates.",
    "Critical. Business-logic flaw where exported verification or canonical-state APIs return or rely on inconsistent mainchain state that downstream bridge or settlement logic could trust incorrectly.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one BTC light client target.

    target_file format:
    "'File Name: contract/src/lib.rs -> Scope: Critical. Acceptance of an invalid, malformed, under-work, cross-network, or incorrectly retargeted block header or chain segment as canonical mainchain state, enabling false confirmations or downstream asset release.'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact BTC light client target:

    {target_file}

    Use live context from the project if available: `BtcLightClient` initialization and migration, `submit_blocks`, `submit_block_header`, `submit_block_header_inner`, `reorg_chain`, `run_mainchain_gc`, `verify_transaction_inclusion`, `verify_transaction_inclusion_v2`, `get_last_n_blocks_hashes`, `get_height_by_block_hash`, `Header::block_hash`, `Header::block_hash_pow`, `target_from_bits`, `work_from_bits`, `U256`, `compute_root_from_merkle_proof`, Dogecoin `check_aux`, Zcash `zcash_get_next_work_required`, relayer `sign_submit_blocks`, `submit_blocks`, `check_submission_skipped`, adaptive batch sizing, and JSON-RPC response parsing.

    Protocol focus:
    This repository implements a NEAR-based light client and relayer stack for Bitcoin-family chains. The contract stores canonical block headers, validates proof-of-work and chain-specific consensus rules, tracks accumulated chainwork for fork choice, prunes historical headers, and verifies transaction inclusion proofs. Feature-gated modules cover Bitcoin, Litecoin, Dogecoin AuxPoW, and Zcash Equihash. Supporting crates handle header parsing, hashing, network configuration, target and work math, and Merkle proof computation. The relayer signs and submits header batches to the contract over NEAR RPC. The audit target is production repository behavior only.

    Analyst mindset:

    * Think like an exploit engineer, not a linter.
    * Infer the file's role first, then generate only questions that fit that role.
    * Reason in exact state transitions: which canonical tip, height-to-hash entry, hash-to-height entry, `headers_pool` entry, `chain_work`, proof root, nonce, signed transaction, or configuration assumption changes before and after the exploit path.
    * Prefer questions that can produce false header acceptance, false inclusion verification, unauthorized protected control, or persistent canonical-state corruption in the fewest realistic steps.
    * If the file is a library or interface, target only reachable integration failures in production callers that depend on it.
    * Use concrete project mechanisms when relevant: mainchain tip promotion, fork storage, difficulty retarget, median-time-past, local-time checks, AuxPoW chain merkle roots, coinbase commitments, Equihash verification, endianness conversions, header decoding, GC windows, proof confirmations, NEAR role gating, relayer nonce handling, and batch-splitting behavior.

    Core invariants:

    * The contract must never accept a header, fork, difficulty transition, AuxPoW proof, Equihash solution, or network-specific block that is invalid under the configured chain rules.
    * The canonical chain mappings (`mainchain_tip_blockhash`, `mainchain_initial_blockhash`, height-to-hash, hash-to-height, and `headers_pool`) must remain internally consistent across submission, reorg, migration, and GC.
    * Transaction inclusion verification must never return true for a transaction or position that is not actually included in the claimed canonical block with the required confirmations.
    * NEAR-protected flows (`init`, pause bypasses, trusted relayer submission, GC exceptions, upgrades, migration, relayer management) must not be reachable without valid authorization.
    * Cross-module assumptions between header parsing, target and work math, hash construction, merkle computation, relayer batching, and contract state transitions must not drift into false trust or replayed canonical updates.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker may be an unprivileged NEAR caller, proof submitter, user relying on the verification API, caller interacting with public contract methods, recipient contract consuming proof results, or a party supplying adversarial header, merkle, AuxPoW, or transaction data through intended production paths.
    * Do not rely on DAO compromise, relayer-manager compromise, code-deployer compromise, leaked private keys, malicious maintainer or operator, unsupported local configuration, social engineering, or pure network-partition-only claims.
    * Do not assume a malicious Bitcoin, Litecoin, Dogecoin, Zcash, or NEAR node unless the exact file shows a project-side validation failure that still makes the exploit real.
    * Exclude denial of service, liveness failures, gas-only issues, dependency-only issues, code style, best-practice findings, harmless view mismatches with no security impact, and theoretical-only findings.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, authorization, replay, chain-selection, proof-validation, arithmetic, serialization, batch-ordering, or cross-module questions.
    * Every question must be testable by a runnable `cargo test` unit test, `near-sdk` contract test, relayer integration test, fuzz test, state-machine test, or transaction-sequence PoC.
    * Avoid generic checklist questions and repeated root causes.
    * Each question must target a plausible issue class for the exact file and scope.
    * Each question must anchor to concrete symbols when possible: function names, structs, mappings, storage variables, or specific cross-module call sites.
    * Prefer questions that name the exact value that may be corrupted: `mainchain_tip_blockhash`, `mainchain_initial_blockhash`, `chain_work`, `block_height`, `bits`, `merkle_root`, `tx_index`, `coinbase_merkle_proof`, `chain_merkle_proof`, `nonce`, access-key nonce, batch boundaries, or expected network config fields.
    * At least half of the questions should require tracing across 2 or more modules or 2 or more functions in sequence.
    * Do not waste slots on low-information prompts such as "is there reentrancy?" or "can math break?" without a concrete BTC light client path and invariant.
    * For arithmetic ideas, focus on endianness, signedness, casts, shifting, target and work conversion, overflow edges, interval boundaries, pow-limit clamps, median windows, and edge-case transitions near zero, max, min, or height boundaries.
    * For proof ideas, focus on merkle-branch shape, duplicated leaves, odd-length trees, index interpretation, internal-node forgery, coinbase proof coupling, and canonical-block confirmation checks.
    * For reordering ideas, focus on attacker-controlled sequencing of header submission, fork promotion, GC execution, migration, relayer batch submission, or access-key nonce consumption.

    High-value attack surfaces:

    * `contract/src/lib.rs`: initialization, header submission, canonical maps, fork promotion, GC, inclusion verification, and migration.
    * `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`, `contract/src/zcash.rs`: chain-specific consensus validation, difficulty retarget, timestamps, AuxPoW, and Equihash.
    * `btc-types/src/*`: header serialization and parsing, hash construction, network configs, AuxPoW structures, target and work math, and `U256`.
    * `merkle-tools/src/lib.rs`: merkle proof generation and root reconstruction.
    * `relayer/src/main.rs`, `near_client.rs`, `bitcoin_client.rs`, `adaptive_batch.rs`, `config.rs`: batching, skip logic, nonce use, RPC parsing, signed transactions, and contract response handling.

    Impact mapping:

    * Critical only: invalid canonical header acceptance; false transaction inclusion; unauthorized protected contract control; corrupted canonical chain mappings; unsafe genesis or migration state; or cross-module desynchronization that creates false trust in chain state or verification results.

    Question quality bar:

    * A strong question names the actor, entrypoint, manipulated state, missing or bypassed check, and concrete bad outcome.
    * A weak question is generic, single-function-only without impact, or does not identify what exact invariant fails.
    * Prefer one sharp question about a realistic exploit chain over several vague variants of the same bug class.

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an attacker ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: run a Rust unit, integration, fuzz, or state test over PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused BTC light client exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production BTC light client repository code listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, generated files, repo automation scripts, configs, build files, deployment-only files, and local tooling.

## Objective
Decide whether the question leads to a real, reachable BTC light client vulnerability.
The attacker must enter through a supported production path: a public or externally reachable NEAR contract call, proof-verification request, initialization or migration path, trusted-relayer submission path with attacker-relevant data, relayer-signed transaction flow, or another externally reachable transaction or data-validation path.
The impact must match the provided target scope.
Prefer #NoVulnerability unless the path is concrete, locally testable on an unmodified Rust or NEAR test setup, and proves one of the Critical impacts in `target_scopes`.
Treat the question as a hypothesis that must survive adversarial review. Look for the exact storage mutation, proof result, authorization bypass, or broken chain-selection identity that would make the exploit real.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production repository files and functions.
3. Check relevant guards: NEAR access control, trusted relayer roles, `#[private]` and `#[init]` restrictions, pause gates, proof confirmation checks, canonical-chain membership checks, header decoding, difficulty and timestamp validation, AuxPoW or Equihash validation, chainwork updates, relayer access-key nonce handling, batch splitting, and RPC response parsing.
4. Identify the exact state variables, proof outputs, or cross-module assumptions that must change for the exploit to work.
5. Decide whether the questioned invariant can actually break under intended deployment.
6. Prove root cause with file, function, and line references.
7. Confirm realistic likelihood and exact scoped impact.
8. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires DAO or privileged-role control, relayer-manager compromise, code-deployer compromise, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- Only affects tests, docs, configs, scripts, mocks, generated code, local tooling, or deployment choices.
- External dependency behavior is the only cause.
- Impact is denial of service, gas griefing, performance degradation, harmless revert behavior, logging noise, observability only, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.
- No exact storage delta, proof delta, or broken invariant can be named.
- The question depends on impossible chain behavior or privileges not granted by the scoped code path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for the BTC light client repository.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production BTC light client repository files in the provided scope as accessible context.
- Do not claim missing or inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, generated files, repo automation scripts, local tooling, or deployment-only choices as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on this repository's security impact.
Focus on externally reachable issues triggered by an unprivileged NEAR caller, proof submitter, relayer-path user supplying adversarial chain data, recipient contract consuming verification results, or another supported production entrypoint.
Only report an analog if this repository has its own reachable root cause and the impact matches the provided target scope.
Be strict about analog quality: similarity of bug class is not enough. This repository must have its own concrete trigger, broken invariant, and scoped impact.

## Method
1. Classify vuln type: invalid header acceptance, fork-choice corruption, proof-verification forgery, role bypass, unsafe migration or init, AuxPoW or Equihash validation flaw, arithmetic or endianness bug, relayer replay or batch-order issue, or cross-module desynchronization.
2. Map to exact production files and modules.
3. Identify the exact state delta, proof result, or canonical-chain value that the analog would corrupt.
4. Prove root cause with exact file, function, module, and line references.
5. Confirm concrete scoped impact and realistic likelihood.
6. Explain the attacker-controlled entry path and why repository code is a necessary vulnerable step.
7. Reject if the impact does not match the provided target scope.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires privileged-role control, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- External dependency behavior is the only cause.
- Test, docs, config, build, generated, or local-tooling issue.
- Theoretical-only issue with no protocol impact.
- Impact is denial of service, gas or performance-only degradation, local misconfiguration, observability noise, harmless rejection, or non-security correctness.
- Impact or likelihood missing.
- No exact corrupted canonical mapping, chainwork value, header acceptance decision, proof result, nonce usage, or authorization assumption can be identified.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict BTC light client validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Validate against this repository's production BTC light client scope and the allowed impact classes below.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject DAO-only, privileged-role-only, leaked-key, best-practice, docs or style, gas-only, denial-of-service, performance-only, griefing-only, dependency-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged external user through normal contract calls, proof-verification flows, attacker-relevant header or proof submission flows, relayer transaction assembly paths, or another supported production entrypoint.
- The final impact must match one of the Critical `target_scopes`, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.
- Be skeptical of reports that describe a bug class without naming the exact state corruption, proof result, or privilege change produced by the exploit.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Acceptance of an invalid, malformed, under-work, cross-network, or incorrectly retargeted block header or chain segment as canonical mainchain state, enabling false confirmations or downstream asset release.
- Critical. Fork-choice, reorg, chainwork, or canonical-mapping corruption that promotes a lower-work fork, rewrites the wrong heights, or desynchronizes tip, hash, and height state used for verification.
- Critical. Merkle proof or transaction inclusion validation flaw that returns true for a nonexistent transaction, wrong index, wrong block, forged internal node, or otherwise invalid inclusion claim.
- Critical. Initialization, migration, or genesis-bootstrap flaw that lets an attacker install unsafe starting state, bypass required private or init constraints, or preserve corrupted storage so invalid chain history becomes trusted.
- Critical. Authorization or role-bypass in NEAR contract entrypoints that enables unauthorized block submission, pause bypass, garbage-collection control, upgrade, migration, init control, or other trusted-relayer or DAO protected actions.
- Critical. Garbage-collection, confirmation-window, or historical-header retention bug that deletes, rewrites, or misclassifies canonical history in a way that causes false verification, permanent proof lockout, or chain-state corruption.
- Critical. Arithmetic, endianness, serialization, hashing, or U256, target, or work conversion flaw that miscomputes proof-of-work validity, chainwork, header identity, difficulty, or merkle roots.
- Critical. Dogecoin AuxPoW validation flaw that accepts an invalid parent block, chain merkle branch, coinbase commitment, chain ID, or expected index, causing false canonical header acceptance.
- Critical. Zcash, Bitcoin, or Litecoin consensus-rule enforcement bug, including Equihash, timestamp windows, difficulty adjustment, min-difficulty handling, or version gating, that admits headers invalid under the intended network rules.
- Critical. Cross-module desynchronization between btc-types, merkle-tools, contract state, and relayer transaction assembly, nonce, or batch logic that causes replayed, skipped, mis-ordered, or incorrectly trusted canonical updates.
- Critical. Business-logic flaw where exported verification or canonical-state APIs return or rely on inconsistent mainchain state that downstream bridge or settlement logic could trust incorrectly.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear root cause and broken protocol, authorization, canonical-state, or proof-validation assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks or guards reviewed and shown insufficient.
5. Exact corrupted state or value delta identified: what canonical hash mapping, chainwork, block height, proof result, nonce, role boundary, or config value changed incorrectly.
6. Concrete impact that exactly matches one allowed repository impact above, with realistic likelihood.
7. Reproducible proof path: Rust unit test, `near-sdk` test, transaction sequence, fuzz or invariant harness, or a justified local reproducer.
8. No obvious rejection reason from privileges, assumptions, known behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or recipient contract trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by this protocol, not by an external dependency alone?
- Is the protocol-state impact concrete, not hypothetical?
- What exact storage fields, canonical mappings, proof results, or signed transaction fields are wrong after the exploit?
- What invariant equation, chain-selection rule, proof rule, or authorization rule is broken?
- Would a security triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz, invariant, or state test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
