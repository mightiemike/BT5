# Q1072: Stale or double-applied subaccountIds

## Question
Can attacker-controlled sequencing make core/contracts/Endpoint.sol / depositCollateral(bytes12 subaccountName, uint32 productId, uint128 amount) consume stale subaccountIds or apply the same subaccountIds transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateral(bytes12 subaccountName, uint32 productId, uint128 amount)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale subaccountIds before all related state is finalized.
- Invariant to test: Slow-mode queue execution must not execute stale, duplicated, or semantically different state transitions.
- Expected HackenProof impact: Critical/High: unauthorized transaction execution through queue, sequencing, or delegatecall confusion.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
