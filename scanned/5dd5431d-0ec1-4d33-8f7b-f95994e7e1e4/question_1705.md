# Q1705: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/Endpoint.sol / executeSlowModeTransaction(...) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/Endpoint.sol / executeSlowModeTransaction(...)
- Entrypoint: User waits for a signed batch that eventually reaches Endpoint.processTransaction(...) via the sequencer path.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/Endpoint.sol / executeSlowModeTransaction(...) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
