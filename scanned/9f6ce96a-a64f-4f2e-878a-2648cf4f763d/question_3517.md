# Q3517: Stale cache or memoized-state window

## Question
Can core/contracts/Clearinghouse.sol / nlpProfitShare(bytes32 poolSubaccount, bytes32 recipient, uint128 amount) read a cached market, health, fee, builder, funding, or balance value that becomes stale before the rest of execution finishes, so later steps act on assumptions that are no longer true?

## Target
- File/function: core/contracts/Clearinghouse.sol / nlpProfitShare(bytes32 poolSubaccount, bytes32 recipient, uint128 amount)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Identify any state snapshot, cached struct, or copied market state used across multiple branches in core/contracts/Clearinghouse.sol / nlpProfitShare(bytes32 poolSubaccount, bytes32 recipient, uint128 amount); then mutate the underlying live state through a reachable interleaving and compare the cached result to fresh reads.
- Invariant to test: A cached or memoized view of state must not remain valid across later user-reachable transitions that can change the economic outcome.
- Expected HackenProof impact: Critical/High: reordering or logic attack through stale cached state.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
