# Q2588: Ordering dependency around nSubmissions

## Question
Can an attacker manipulate reachable call order so that core/contracts/Endpoint.sol / submitTransactionsChecked(uint64 idx, bytes[] calldata transactions, bytes32 e, bytes32 s, uint8 signerBitmask) observes nSubmissions in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/Endpoint.sol / submitTransactionsChecked(uint64 idx, bytes[] calldata transactions, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User calls Endpoint.depositCollateral(...) directly.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Reorder the same user actions around nSubmissions, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Slow-mode queue execution must not execute stale, duplicated, or semantically different state transitions.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
