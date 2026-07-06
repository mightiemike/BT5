# Q2366: Stale or double-applied slowModeConfig

## Question
Can attacker-controlled sequencing make core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) consume stale slowModeConfig or apply the same slowModeConfig transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale slowModeConfig before all related state is finalized.
- Invariant to test: Slow-mode queue execution must not execute stale, duplicated, or semantically different state transitions.
- Expected HackenProof impact: Critical/High: unauthorized transaction execution through queue, sequencing, or delegatecall confusion.
- Fast validation: Queue multiple slow-mode actions, manipulate ordering and timing, and assert each item executes once and only for its intended sender/state.
