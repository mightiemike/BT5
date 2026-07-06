import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
SOURCE_REPO = "nadohq/nado-contracts"
REPO_NAME = "nado-contracts"
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
    'core/contracts/Airdrop.sol',
    'core/contracts/BaseEngine.sol',
    'core/contracts/BaseProxyManager.sol',
    'core/contracts/BaseWithdrawPool.sol',
    'core/contracts/Clearinghouse.sol',
    'core/contracts/ClearinghouseLiq.sol',
    'core/contracts/ClearinghouseStorage.sol',
    'core/contracts/ContractOwner.sol',
    'core/contracts/DirectDepositV1.sol',
    'core/contracts/Endpoint.sol',
    'core/contracts/EndpointGated.sol',
    'core/contracts/EndpointStorage.sol',
    'core/contracts/EndpointTx.sol',
    'core/contracts/OffchainExchange.sol',
    'core/contracts/PerpEngine.sol',
    'core/contracts/PerpEngineState.sol',
    'core/contracts/ProxyManager.sol',
    'core/contracts/SpotEngine.sol',
    'core/contracts/SpotEngineState.sol',
    'core/contracts/Verifier.sol',
    'core/contracts/WithdrawPool.sol',
    'core/contracts/common/Constants.sol',
    'core/contracts/common/Errors.sol',
    'core/contracts/interfaces/IAirdrop.sol',
    'core/contracts/interfaces/IERC20Base.sol',
    'core/contracts/interfaces/IERC4626Base.sol',
    'core/contracts/interfaces/IEndpoint.sol',
    'core/contracts/interfaces/IEndpointGated.sol',
    'core/contracts/interfaces/IFEndpoint.sol',
    'core/contracts/interfaces/IOffchainExchange.sol',
    'core/contracts/interfaces/IProxyManager.sol',
    'core/contracts/interfaces/IVerifier.sol',
    'core/contracts/interfaces/clearinghouse/IClearinghouse.sol',
    'core/contracts/interfaces/clearinghouse/IClearinghouseEventEmitter.sol',
    'core/contracts/interfaces/clearinghouse/IClearinghouseLiq.sol',
    'core/contracts/interfaces/engine/IPerpEngine.sol',
    'core/contracts/interfaces/engine/IProductEngine.sol',
    'core/contracts/interfaces/engine/ISpotEngine.sol',
    'core/contracts/libraries/ERC20Helper.sol',
    'core/contracts/libraries/MathHelper.sol',
    'core/contracts/libraries/MathSD21x18.sol',
    'core/contracts/libraries/RiskHelper.sol',
]

target_scopes = [
    "Critical. Unauthorized mutation of another user's subaccount balances, positions, signer linkage, withdrawal rights, or other protected trading state.",
    "Critical. Theft, permanent lock, or unbacked creation/duplication of collateral, quote balances, insurance funds, NLP or LP value, withdrawal claims, or protocol-controlled assets.",
    "Critical. Solvency or accounting failure in health checks, liquidation, settlement, funding, fees, insurance, or spread handling that transfers value incorrectly or leaves the protocol undercollateralized.",
    "Critical. Signature, nonce, quorum, sequencer, slow-mode, delegatecall, or authentication bypass that enables unauthorized privileged actions, replayed state changes, or forged order execution.",
    "Critical. Unauthorized owner, admin, proxy-manager, verifier, or endpoint-equivalent control path that can seize funds, reroute assets, or mutate protected protocol configuration/state.",
    "Critical. Cross-contract state desynchronization among Endpoint, EndpointTx, Clearinghouse, SpotEngine, PerpEngine, OffchainExchange, Verifier, WithdrawPool, ProxyManager, DirectDepositV1, or Airdrop that causes unauthorized release, loss, lock, or duplication of funds/state.",
    "Critical. Unauthorized transaction execution that submits, confirms, replays, or settles actions on behalf of a user without valid consent, authorization, or signer ownership.",
    "Critical. Transaction manipulation that changes order parameters, recipient routing, price or amount semantics, liquidation terms, or settlement outcomes in a way that transfers value incorrectly.",
    "Critical. Business-logic flaw where contract behavior diverges from intended protocol rules and enables asset theft, locked funds, incorrect liquidation, bad debt creation, or unauthorized privileged outcomes.",
    "Critical. Reentrancy across token transfers, callbacks, delegatecalls, withdrawal flows, or cross-contract execution that enables double-withdrawal, double-credit, stale-state execution, or balance bypass.",
    "Critical. Reordering or sequence-dependence flaw where attacker-controlled call ordering, queue ordering, nonce ordering, or settlement ordering produces unauthorized state transitions or value extraction.",
    "Critical. Arithmetic, scaling, precision, overflow, or underflow flaw in balances, funding, fees, health, pricing, settlement, or share accounting that creates or destroys value incorrectly or breaks solvency.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one Nado target.

    target_file format:
    "'File Name: core/contracts/Endpoint.sol -> Scope: Critical. Unauthorized mutation of another user's subaccount balances, positions, signer linkage, withdrawal rights, or other protected trading state.'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Nado protocol target:

    {target_file}

    Use live context from the project if available: Endpoint and EndpointTx transaction routing, slow-mode execution, EIP-712 signed actions, verifier quorum checks, clearinghouse health and liquidation logic, spot/perp engine accounting, isolated subaccounts, withdraw pools, proxy management, direct deposits, airdrops, token transfer helpers, decimals conversion, and cross-contract state synchronization.

    Protocol focus:
    Nado is an EVM-based trading protocol with a central Endpoint for user actions, a Clearinghouse that enforces collateral and health invariants, SpotEngine and PerpEngine for balance and position accounting, OffchainExchange for signed order execution and isolated subaccounts, Verifier for quorum-based signature validation, and auxiliary asset-movement contracts such as WithdrawPool, ProxyManager, DirectDepositV1, and Airdrop. The audit target is production Nado protocol smart-contract behavior only.

    Analyst mindset:

    * Think like an exploit engineer, not a linter.
    * Infer the file's role first, then generate only questions that fit that role.
    * Reason in exact state transitions: which storage values, balances, positions, signer links, queue pointers, nonces, or ownership assumptions change before and after the exploit path.
    * Prefer questions that can produce attacker profit, unauthorized control, or protocol bad debt in the fewest realistic steps.
    * If the file is a library or interface, target only reachable integration failures in production callers that depend on it.
    * Use concrete Nado mechanisms when relevant: isolated subaccounts, quote/product routing, int128 math, engine balance updates, slow-mode execution, signer bitmasks, delegatecall routing, decimals scaling, and settlement or liquidation state.

    Core invariants:

    * An unprivileged caller or linked signer must never mutate another user's subaccount, signer relationship, balances, positions, or withdrawal destination without valid authorization.
    * Collateral, quote balances, insurance, fees, NLP or LP value, and withdrawal claims must never be stolen, permanently locked, released early, duplicated, or created without backing.
    * Health, liquidation, settlement, spread, funding, and fee accounting must preserve solvency and must not transfer value across users incorrectly.
    * Signature, nonce, quorum, sequencer, slow-mode, and delegatecall flows must not permit replay, forgery, unauthorized execution, or privileged state changes.
    * Cross-contract flows between Endpoint, Clearinghouse, engines, verifier, pools, and deposit helpers must remain synchronized so that assets and state cannot desync into loss, lock, or unauthorized release.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker may be an unprivileged EOA, contract caller, recipient contract, liquidator, trader, linked signer, direct-deposit user, or user crafting signed payloads and calldata.
    * Do not rely on owner/admin compromise, sequencer compromise, verifier key compromise, governance capture, malicious maintainer/operator, leaked private keys, unsupported local configuration, social engineering, or pure MEV/front-running-only claims.
    * Do not assume arbitrary product registration or arbitrary token listing unless the exact file shows an attacker-reachable path to obtain that power first.
    * Exclude denial of service, liveness failures, gas-only issues, dependency-only issues, code style, best-practice findings, harmless rounding dust with no asset impact, and theoretical-only findings.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, authorization, accounting, replay, settlement, liquidation, token-integration, delegatecall, proxy, or cross-module questions.
    * Every question must be testable by a runnable Hardhat unit test, invariant test, fuzz test, state-machine test, or transaction sequence PoC.
    * Avoid generic checklist questions and repeated root causes.
    * Each question must target a plausible issue class for the exact file and scope.
    * Each question must anchor to concrete symbols when possible: function names, structs, mappings, storage variables, or specific cross-contract call sites.
    * Prefer questions that name the exact value that may be corrupted: collateral amount, vQuote, funding, availableSettle, signer bitmask, nonce, txCount, txUpTo, fee tier, isolated subaccount link, balance normalization, or withdrawal destination.
    * At least half of the questions should require tracing across 2 or more contracts or 2 or more functions in sequence.
    * Do not waste slots on low-information prompts such as "is there reentrancy?" or "can math break?" without a concrete Nado path and invariant.
    * For arithmetic ideas, focus on signedness, casts, scaling, decimal normalization, multiplier drift, price-weight interactions, and edge-case transitions near zero, max, min, or negative balances.
    * For token integration ideas, focus on protocol-side assumptions about transfer semantics, callbacks, approval reuse, missing return checks, and asset accounting mismatch rather than bugs in the token itself.
    * For reordering ideas, focus on attacker-controlled sequencing of queue consumption, nonce use, liquidation, settlement, deposit crediting, or isolated-subaccount closure.

    High-value attack surfaces:

    * Endpoint and EndpointTx: transaction decoding, delegatecall routing, slow-mode queueing/execution, signer linking, withdrawal flows, sanctions gates, and nonce/replay handling.
    * Clearinghouse and engines: collateral accounting, transfer restrictions, health checks, liquidation, insurance, spread handling, settlement, funding, decimals conversion, and engine routing.
    * OffchainExchange: signed order execution, isolated subaccounts, fee tiers, builder fees, parent/child subaccount links, and market configuration.
    * Verifier: EIP-712 digest construction, signer bitmasks, quorum checks, aggregate pubkey caching, and signature acceptance rules.
    * Asset custody helpers: WithdrawPool, BaseWithdrawPool, ProxyManager, BaseProxyManager, DirectDepositV1, and Airdrop.
    * Token interactions: ERC20 or ERC4626 transfers, approvals, callbacks, transfer return-value handling, and token decimal assumptions.

    Impact mapping:

    * Critical only: unauthorized subaccount or signer-state mutation; theft, lock, or duplication of collateral or protocol assets; solvency-breaking accounting errors; signature or authentication bypass enabling unauthorized privileged execution; unauthorized control-path escalation; or cross-contract desynchronization causing fund or state loss.

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
    "[File: {target_file}] [Function: symbol_or_module] Can an attacker ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Nado exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production Nado protocol smart-contract code listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, generated files, repo automation scripts, configs, build files, ABI outputs, deployment-only files, and local tooling.

## Objective
Decide whether the question leads to a real, reachable Nado vulnerability.
The attacker must enter through a supported production path: a public or external contract call, signed order or withdrawal flow, slow-mode flow, liquidation path, direct-deposit path, token callback or transfer interaction, or another externally reachable protocol transaction path.
The impact must match the provided target scope.
Prefer #NoVulnerability unless the path is concrete, locally testable on an unmodified Hardhat setup, and proves one of the Critical impacts in `target_scopes`.
Treat the question as a hypothesis that must survive adversarial review. Look for the exact storage mutation, asset delta, authorization bypass, or broken accounting identity that would make the exploit real.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production Nado files/functions.
3. Check relevant Nado guards: access control, endpoint gating, nonce handling, EIP-712 digest construction, signature/quorum validation, balance and health accounting, liquidation rules, settlement logic, token transfer handling, subaccount ownership checks, isolated-subaccount constraints, and proxy/delegatecall boundaries.
4. Identify the exact state variables, balances, or cross-contract assumptions that must change for the exploit to work.
5. Decide whether the questioned invariant can actually break under intended deployment.
6. Prove root cause with file/function/line references.
7. Confirm realistic likelihood and exact scoped impact.
8. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires owner/admin control, sequencer compromise, verifier key compromise, governance capture, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- Only affects tests, docs, configs, scripts, mocks, generated code, ABI outputs, local tooling, or deployment choices.
- External dependency behavior is the only cause.
- Impact is denial of service, gas griefing, performance degradation, harmless revert behavior, rounding dust with no user or protocol asset impact, logging noise, observability only, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.
- No exact storage delta, asset delta, or broken invariant can be named.
- The question depends on impossible token behavior or privileges not granted by the scoped code path.

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
    Generate a short cross-project analog scan prompt for Nado.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production Nado Solidity files in the provided scope as accessible context.
- Do not claim missing or inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, ABI outputs, IDE files, configs, generated files, repo automation scripts, local tooling, or deployment-only choices as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on Nado protocol security impact.
Focus on externally reachable Nado issues triggered by an unprivileged caller, trader, liquidator, linked signer, recipient contract, or user interacting through supported contract entrypoints.
Only report an analog if Nado code has its own reachable root cause and the impact matches the provided target scope.
Be strict about analog quality: similarity of bug class is not enough. Nado must have its own concrete trigger, broken invariant, and scoped impact.

## Method
1. Classify vuln type: unauthorized subaccount mutation, signer or replay bypass, collateral theft or lock, solvency/accounting corruption, liquidation or settlement bypass, delegatecall or proxy abuse, verifier or digest flaw, token-transfer integration bug, or cross-contract desynchronization.
2. Map to Nado components and exact production files.
3. Identify the exact Nado state delta or asset delta that the analog would corrupt.
4. Prove root cause with exact file/function/module/line references.
5. Confirm concrete Nado scoped impact and realistic likelihood.
6. Explain the attacker-controlled entry path and why Nado code is a necessary vulnerable step.
7. Reject if the impact does not match the provided target scope.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires owner/admin control, sequencer compromise, verifier key compromise, governance capture, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- External dependency behavior is the only cause.
- Test/docs/config/build/generated/local-tooling issue.
- Theoretical-only issue with no protocol impact.
- Impact is denial of service, gas or performance-only degradation, rounding-only drift without meaningful asset impact, local misconfiguration, observability noise, harmless rejection, or non-security correctness.
- Impact or likelihood missing.
- No exact corrupted balance, signer state, nonce, queue state, settlement value, or ownership assumption can be identified.

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
    Generate a strict Nado protocol validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Validate against this repository's production Nado contract scope and the allowed impact classes below.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject owner-only, admin-only, sequencer-compromise, verifier-key-compromise, governance-only, leaked-key, best-practice, docs/style, gas-only, denial-of-service, performance-only, griefing-only, front-running-only, dependency-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged external user through normal contract calls, signed payload flows, liquidation or withdrawal paths, token interaction paths, or another supported protocol entrypoint.
- The final impact must match one of the Critical `target_scopes`, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.
- Be skeptical of reports that describe a bug class without naming the exact state corruption, asset movement, or privilege change produced by the exploit.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized mutation of another user's subaccount balances, positions, signer linkage, withdrawal rights, or other protected trading state.
- Critical. Theft, permanent lock, or unbacked creation/duplication of collateral, quote balances, insurance funds, NLP or LP value, withdrawal claims, or protocol-controlled assets.
- Critical. Solvency or accounting failure in health checks, liquidation, settlement, funding, fees, insurance, or spread handling that transfers value incorrectly or leaves the protocol undercollateralized.
- Critical. Signature, nonce, quorum, sequencer, slow-mode, delegatecall, or authentication bypass that enables unauthorized privileged actions, replayed state changes, or forged order execution.
- Critical. Unauthorized owner, admin, proxy-manager, verifier, or endpoint-equivalent control path that can seize funds, reroute assets, or mutate protected protocol configuration/state.
- Critical. Cross-contract state desynchronization among Endpoint, EndpointTx, Clearinghouse, SpotEngine, PerpEngine, OffchainExchange, Verifier, WithdrawPool, ProxyManager, DirectDepositV1, or Airdrop that causes unauthorized release, loss, lock, or duplication of funds/state.
- Critical. Unauthorized transaction execution that submits, confirms, replays, or settles actions on behalf of a user without valid consent, authorization, or signer ownership.
- Critical. Transaction manipulation that changes order parameters, recipient routing, price or amount semantics, liquidation terms, or settlement outcomes in a way that transfers value incorrectly.
- Critical. Business-logic flaw where contract behavior diverges from intended protocol rules and enables asset theft, locked funds, incorrect liquidation, bad debt creation, or unauthorized privileged outcomes.
- Critical. Reentrancy across token transfers, callbacks, delegatecalls, withdrawal flows, or cross-contract execution that enables double-withdrawal, double-credit, stale-state execution, or balance bypass.
- Critical. Reordering or sequence-dependence flaw where attacker-controlled call ordering, queue ordering, nonce ordering, or settlement ordering produces unauthorized state transitions or value extraction.
- Critical. Arithmetic, scaling, precision, overflow, or underflow flaw in balances, funding, fees, health, pricing, settlement, or share accounting that creates or destroys value incorrectly or breaks solvency.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol, authorization, or accounting assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks or guards reviewed and shown insufficient.
5. Exact corrupted state or value delta identified: what balance, position, nonce, signer relation, queue item, settlement value, or config changed incorrectly.
6. Concrete impact that exactly matches one allowed Nado impact above, with realistic likelihood.
7. Reproducible proof path: Hardhat test, transaction sequence, fuzz or invariant harness, or a justified local reproducer.
8. No obvious rejection reason from privileges, assumptions, known behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or recipient contract trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by this protocol, not by an external dependency alone?
- Is the account, asset, or protocol-state impact concrete, not hypothetical?
- What exact storage fields or balances are wrong after the exploit?
- What invariant equation or authorization rule is broken?
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
[Concrete allowed Nado impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/state test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
