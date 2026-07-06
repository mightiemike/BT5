# Q2788: Stale or double-applied priceX18

## Question
Can attacker-controlled sequencing make core/contracts/Endpoint.sol / submitTransactionsCheckedWithGasLimit(uint64 idx, bytes[] calldata transactions, uint256 gasLimit) consume stale priceX18 or apply the same priceX18 transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Endpoint.sol / submitTransactionsCheckedWithGasLimit(uint64 idx, bytes[] calldata transactions, uint256 gasLimit)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale priceX18 before all related state is finalized.
- Invariant to test: Slow-mode queue execution must not execute stale, duplicated, or semantically different state transitions.
- Expected HackenProof impact: Critical/High: unauthorized transaction execution through queue, sequencing, or delegatecall confusion.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
