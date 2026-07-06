# Q2724: Ordering dependency around block.timestamp

## Question
Can an attacker manipulate reachable call order so that core/contracts/Endpoint.sol / submitTransactionsCheckedWithGasLimit(uint64 idx, bytes[] calldata transactions, uint256 gasLimit) observes block.timestamp in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/Endpoint.sol / submitTransactionsCheckedWithGasLimit(uint64 idx, bytes[] calldata transactions, uint256 gasLimit)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Reorder the same user actions around block.timestamp, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Slow-mode queue execution must not execute stale, duplicated, or semantically different state transitions.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
