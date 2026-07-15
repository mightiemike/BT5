import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "near/nearcore"
REPO_NAME = "nearcore"
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
    "src/allocator.rs",
    "src/bls_ops.rs",
    "src/chia_dialect.rs",
    "src/core_ops.rs",
    "src/cost.rs",
    "src/dialect.rs",
    "src/error.rs",
    "src/f_table.rs",
    "src/keccak256_ops.rs",
    "src/lib.rs",
    "src/more_ops.rs",
    "src/number.rs",
    "src/op_utils.rs",
    "src/reduction.rs",
    "src/run_program.rs",
    "src/runtime_dialect.rs",
    "src/secp_ops.rs",
    "src/serde/bitset.rs",
    "src/serde/bytes32.rs",
    "src/serde/de.rs",
    "src/serde/de_br.rs",
    "src/serde/de_tree.rs",
    "src/serde/identity_hash.rs",
    "src/serde/incremental.rs",
    "src/serde/intern.rs",
    "src/serde/mod.rs",
    "src/serde/object_cache.rs",
    "src/serde/parse_atom.rs",
    "src/serde/path_builder.rs",
    "src/serde/read_cache_lookup.rs",
    "src/serde/ser.rs",
    "src/serde/ser_br.rs",
    "src/serde/serialized_length.rs",
    "src/serde/tools.rs",
    "src/serde/tree_cache.rs",
    "src/serde/utils.rs",
    "src/serde/write_atom.rs",
    "src/serde_2026/de.rs",
    "src/serde_2026/mod.rs",
    "src/serde_2026/ser.rs",
    "src/serde_2026/strategy.rs",
    "src/serde_2026/varint.rs",
    "src/sha_tree_op.rs",
    "src/traverse_path.rs",
    "src/treehash.rs",
    "wheel/src/adapt_response.rs",
    "wheel/src/api.rs",
    "wheel/src/lazy_node.rs",
    "wheel/src/lib.rs",
    "wheel/python/clvm_rs/__init__.py",
    "wheel/python/clvm_rs/at.py",
    "wheel/python/clvm_rs/casts.py",
    "wheel/python/clvm_rs/chia_dialect.py",
    "wheel/python/clvm_rs/clvm_storage.py",
    "wheel/python/clvm_rs/clvm_tree.py",
    "wheel/python/clvm_rs/curry_and_treehash.py",
    "wheel/python/clvm_rs/de.py",
    "wheel/python/clvm_rs/eval_error.py",
    "wheel/python/clvm_rs/program.py",
    "wheel/python/clvm_rs/replace.py",
    "wheel/python/clvm_rs/ser.py",
    "wheel/python/clvm_rs/serde.py",
    "wheel/python/clvm_rs/tree_hash.py",
]

target_scopes = [
    "Critical. Consensus divergence in CLVM execution, cost accounting, dialect flags, operator semantics, or tree hashing lets the same spend/program be accepted by one node or binding and rejected or evaluated differently by another.",
    "Critical. Canonical serialization, backrefs, serde_2026, deserialization, or serialized-length logic accepts non-canonical bytes, produces a different tree than expected, or maps distinct byte streams to the same consensus object.",
    "Critical. BLS, secp256k1, secp256r1, keccak256, sha256tree, or tree-hash operation bug validates an invalid signature/hash/proof or rejects a valid one in a consensus-relevant path.",
    "Critical. Allocator, NodePtr, interning, lazy-node, object-cache, tree-cache, or path traversal flaw corrupts CLVM tree identity, pair/atom boundaries, or referenced nodes in a way that changes program meaning.",
    "High. Cost, memory-limit, stack-limit, softfork guard, operator-set, or fast-path/fallback mismatch permits undercharged execution or bypasses intended CLVM limits with consensus or mempool acceptance impact.",
    "High. Numeric atom parsing, signed/unsigned conversion, division/modulo, shift, comparison, or small-integer fast path produces a result that differs from CLVM specification or generic big-integer behavior.",
    "High. Python wheel or Rust API binding exposes CLVM execution, serialization, lazy nodes, or tree conversion behavior that differs from the Rust core and can mislead wallet, mempool, or consensus-adjacent callers.",
    "High. Incremental serialization/deserialization, object-cache reuse, tree-cache reuse, or read-cache lookup bug returns stale or incorrect nodes, hashes, lengths, or backreferences across attacker-controlled inputs.",
    "High. Dialect, flag, or operator-table wiring enables disabled operators, omits enabled operators, mishandles mempool mode, or applies the wrong Chia CLVM semantics for a reachable caller.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one clvm_rs target.

    target_file format:
    "'File Name: src/run_program.rs -> Scope: Critical. Consensus divergence in CLVM execution, cost accounting, dialect flags, operator semantics, or tree hashing lets the same spend/program be accepted by one node or binding and rejected or evaluated differently by another.'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact clvm_rs target:

    {target_file}

    Project focus:
    This repository is `clvm_rs`, the Rust CLVM interpreter used by Chia software, with Python wheel bindings. Security impact is consensus or consensus-adjacent correctness: CLVM program result, cost, accepted/rejected serialization, tree hash, signature/hash operation result, dialect flag behavior, allocator node identity, or Python/Rust API equivalence changing for attacker-controlled CLVM bytes, atoms, programs, or environments.

    Use concrete project mechanisms when relevant: `Allocator`, `NodePtr`, atoms/pairs, `run_program`, `ChiaDialect`, `ClvmFlags`, operator tables, softfork guards, `traverse_path`, numeric atoms, `op_utils`, BLS/secp/keccak/sha/tree ops, `node_from_bytes`, backrefs, `serialized_length_from_bytes`, `parse_triples`, `tree_hash`, `serde_2026`, object/tree caches, `LazyNode`, `adapt_response`, and Python conversion helpers.

    Analyst mindset:

    * Think like an exploit engineer finding consensus splits, not a linter.
    * Infer the file's role first, then generate only questions that fit that role and the given scope.
    * Reason in transitions: bytes -> nodes -> execution -> cost/result/error -> serialization/tree hash/API object.
    * Prefer questions about malformed CLVM, boundary atoms, backrefs, cache reuse, flag combinations, fast-path mismatches, and Python/Rust behavioral differences.
    * If the file is a helper or binding, target reachable production callers that depend on its exact output.

    Core invariants:

    * One canonical CLVM byte stream must deserialize to exactly one intended tree, and serialization/tree hash must preserve that tree.
    * Fast paths, fallback big-integer paths, Rust API, Python API, and dialect modes must agree on result, error, and cost.
    * Crypto and hash ops must accept only valid inputs and produce Chia-compatible outputs.
    * Allocator, interning, caches, lazy nodes, and path traversal must never alias atoms/pairs or return stale nodes that alter meaning.
    * Limits, costs, softfork guards, and operator sets must be applied before any undercharged or disabled behavior becomes observable.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker controls serialized CLVM bytes, program, environment, atoms, flags exposed by callers, Python objects passed to bindings, or repeated API/cache usage.
    * Do not rely on malicious maintainers, compromised nodes, downstream caller misuse outside this API contract, unsupported local config, social engineering, or dependency-only bugs.
    * Exclude ordinary crashes, denial of service, performance-only issues, logging/display bugs, docs/tests/fuzz-harness issues, and best practices without consensus or API-equivalence impact.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step parser/executor/hash/cache/binding/cost/flag/invariant questions.
    * Every question must be testable by `cargo test`, a Rust unit/property test, a fuzz target, a Python wheel test, or a cross-implementation differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Each question must target a plausible issue class for the exact file and scope.
    * Anchor to concrete symbols when possible: function names, structs, flags, caches, NodePtr fields, op names, or API methods.
    * Name the exact value that may diverge: result atom, pair structure, error, cost, tree hash, serialized bytes, parsed triple, signature boolean, operator availability, or LazyNode content.
    * At least half of the questions should require tracing across 2 or more functions/modules.
    * For arithmetic, focus on signedness, minimal atom encoding, zero/negative edge cases, overflow, fast path vs bignum fallback, div/mod, shifts, and comparisons.
    * For serialization, focus on canonical form, backrefs, serde_2026 prefix/body, varints, length accounting, cache lookup, and round-trip/tree-hash equality.

    High-value attack surfaces:

    * `src/run_program.rs`, `src/chia_dialect.rs`, `src/dialect.rs`, `src/f_table.rs`: execution, costs, flags, softforks, operator wiring.
    * `src/core_ops.rs`, `src/more_ops.rs`, `src/op_utils.rs`, `src/number.rs`: CLVM operator semantics and numeric edge cases.
    * `src/serde/*`, `src/serde_2026/*`: canonical bytes, backrefs, incremental parsing, caches, lengths, and tree construction.
    * `src/allocator.rs`, `src/treehash.rs`, `src/traverse_path.rs`, `src/sha_tree_op.rs`: tree identity, traversal, hashing, and atom/pair invariants.
    * `src/bls_ops.rs`, `src/secp_ops.rs`, `src/keccak256_ops.rs`: consensus-visible crypto/hash semantics.
    * `wheel/src/*`, `wheel/python/clvm_rs/*`: Python/Rust API equivalence for execution, lazy nodes, serialization, tree hash, curry, and object conversion.

    Impact mapping:

    * High/Critical only: consensus divergence, incorrect acceptance/rejection, wrong CLVM result/cost/hash/signature result, non-canonical serialization acceptance, stale/corrupt node identity, or Python/Rust API mismatch that can affect wallet, mempool, or consensus-adjacent decisions.

    Each question must include:

    1. target function/module;
    2. attacker-controlled input;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can attacker-controlled INPUT under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: run a Rust unit/property/fuzz/Python differential test over PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused clvm_rs exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production clvm_rs repository code listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, fuzz harnesses, benches, generated data, repo automation, packaging metadata, and local-only tooling.

## Objective
Decide whether the question leads to a real, reachable clvm_rs vulnerability.
The attacker must enter through supported production behavior: Rust CLVM execution, deserialization/serialization, tree hashing, crypto/hash operators, dialect flags, allocator/tree APIs, Python wheel bindings, or another public API reachable with attacker-controlled CLVM data.
The impact must match the provided target scope.
Prefer #NoVulnerability unless the path is concrete, locally testable, and proves High/Critical consensus, mempool, wallet, or API-equivalence impact.
Treat the question as a hypothesis that must survive adversarial review. Look for the exact result, cost, error, tree, hash, serialized bytes, operator availability, signature result, or LazyNode value that would make the exploit real.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production repository files and functions.
3. Check relevant guards: canonical parsing, backref validation, serde_2026 prefix/body checks, cost and stack limits, heap flags, dialect/operator flags, softfork guards, numeric normalization, crypto input checks, cache keys, allocator checkpoints, and Python conversion rules.
4. Identify the exact state or output that must change for the exploit to work.
5. Decide whether the questioned invariant can actually break under intended API use.
6. Prove root cause with file, function, and line references.
7. Confirm realistic likelihood and exact scoped impact.
8. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires malicious maintainers, compromised nodes, downstream misuse outside this API contract, unsupported local configuration, social engineering, or dependency-only behavior.
- Only affects tests, docs, mocks, fuzz harnesses, benches, generated data, automation, packaging, or local tooling.
- Impact is ordinary crash, denial of service, performance-only degradation, logging/display issue, harmless rejection, style, or best practice.
- No concrete scoped impact or no realistic attacker-controlled API path.
- No exact result, cost, error, tree hash, serialized bytes, signature result, operator flag, cache entry, NodePtr, or LazyNode delta can be named.
- The question depends on impossible CLVM behavior or privileges not granted by the scoped code path.

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
    Generate a short cross-project analog scan prompt for the clvm_rs repository.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production clvm_rs repository files in the provided scope as accessible context.
- Do not claim missing or inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, mocks, fuzz harnesses, benches, generated data, repo automation, packaging metadata, or local-only tooling as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on this repository's security impact.
Focus on externally reachable issues triggered by attacker-controlled CLVM bytes, programs, environments, atoms, dialect flags exposed by callers, Python objects, or repeated serialization/cache/API use.
Only report an analog if this repository has its own reachable root cause and the impact matches the provided target scope.
Be strict about analog quality: similarity of bug class is not enough. This repository must have its own concrete trigger, broken invariant, and scoped impact.

## Method
1. Classify vuln type: consensus divergence, non-canonical serialization, parser confusion, hash/signature mismatch, arithmetic semantic mismatch, undercharged execution, cache/allocator aliasing, flag/operator wiring error, or Python/Rust API divergence.
2. Map to exact production files and modules.
3. Identify the exact result, cost, error, tree, hash, bytes, parsed triple, signature boolean, NodePtr, LazyNode, or operator flag that the analog would corrupt.
4. Prove root cause with exact file, function, module, and line references.
5. Confirm concrete scoped impact and realistic likelihood.
6. Explain the attacker-controlled entry path and why repository code is a necessary vulnerable step.
7. Reject if the impact does not match the provided target scope.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires malicious maintainers, compromised nodes, unsupported local configuration, social engineering, or dependency-only behavior.
- Test, docs, mocks, fuzz harness, bench, generated data, automation, packaging, or local-tooling issue.
- Theoretical-only issue with no consensus, wallet, mempool, or API-equivalence impact.
- Impact is ordinary crash, denial of service, performance-only degradation, logging/display noise, harmless rejection, style, or best practice.
- Impact or likelihood missing.
- No exact corrupted result, cost, error, tree hash, bytes, signature result, NodePtr, LazyNode, cache entry, or flag assumption can be identified.

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
    Generate a strict clvm_rs validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Validate against this repository's production clvm_rs scope and the allowed impact classes below.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-maintainer, compromised-node, downstream-misuse, unsupported-config, docs/style, ordinary-crash, denial-of-service, performance-only, dependency-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, missing external context, or unsupported CLVM behavior.
- A valid report must be triggerable through Rust CLVM APIs, Python wheel APIs, serialization/deserialization, tree hashing, crypto/hash operators, dialect flags, allocator/tree APIs, or another supported production entrypoint.
- The final impact must match one of the High/Critical `target_scopes`, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.
- Be skeptical of reports that describe a bug class without naming the exact result, cost, error, tree, hash, serialized bytes, signature result, NodePtr, LazyNode, cache entry, or flag behavior produced by the exploit.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Consensus divergence in CLVM execution, cost accounting, dialect flags, operator semantics, or tree hashing lets the same spend/program be accepted by one node or binding and rejected or evaluated differently by another.
- Critical. Canonical serialization, backrefs, serde_2026, deserialization, or serialized-length logic accepts non-canonical bytes, produces a different tree than expected, or maps distinct byte streams to the same consensus object.
- Critical. BLS, secp256k1, secp256r1, keccak256, sha256tree, or tree-hash operation bug validates an invalid signature/hash/proof or rejects a valid one in a consensus-relevant path.
- Critical. Allocator, NodePtr, interning, lazy-node, object-cache, tree-cache, or path traversal flaw corrupts CLVM tree identity, pair/atom boundaries, or referenced nodes in a way that changes program meaning.
- High. Cost, memory-limit, stack-limit, softfork guard, operator-set, or fast-path/fallback mismatch permits undercharged execution or bypasses intended CLVM limits with consensus or mempool acceptance impact.
- High. Numeric atom parsing, signed/unsigned conversion, division/modulo, shift, comparison, or small-integer fast path produces a result that differs from CLVM specification or generic big-integer behavior.
- High. Python wheel or Rust API binding exposes CLVM execution, serialization, lazy nodes, or tree conversion behavior that differs from the Rust core and can mislead wallet, mempool, or consensus-adjacent callers.
- High. Incremental serialization/deserialization, object-cache reuse, tree-cache reuse, or read-cache lookup bug returns stale or incorrect nodes, hashes, lengths, or backreferences across attacker-controlled inputs.
- High. Dialect, flag, or operator-table wiring enables disabled operators, omits enabled operators, mishandles mempool mode, or applies the wrong Chia CLVM semantics for a reachable caller.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear root cause and broken consensus, serialization, execution, hashing, crypto, allocator, cache, flag, or API-equivalence assumption.
3. Reachable exploit path: preconditions -> attacker input -> trigger -> bad result.
4. Existing checks or guards reviewed and shown insufficient.
5. Exact corrupted value identified: what result, cost, error, tree, hash, bytes, parsed triple, signature result, NodePtr, LazyNode, cache entry, or flag behavior changed incorrectly.
6. Concrete impact that exactly matches one allowed repository impact above, with realistic likelihood.
7. Reproducible proof path: Rust unit/property test, fuzz target, Python wheel test, cross-implementation differential test, or justified local reproducer.
8. No obvious rejection reason from assumptions, dependency-only behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can attacker-controlled CLVM data or public API input trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by this repository, not by an external dependency alone?
- Is the consensus/API impact concrete, not hypothetical?
- What exact result, cost, error, tree hash, serialized bytes, signature result, NodePtr, LazyNode, cache entry, or flag behavior is wrong after the exploit?
- What serialization rule, CLVM semantic rule, cost rule, tree identity rule, crypto rule, or API-equivalence rule is broken?
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
[Minimal reproducible steps or fuzz, differential, property, or state test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
