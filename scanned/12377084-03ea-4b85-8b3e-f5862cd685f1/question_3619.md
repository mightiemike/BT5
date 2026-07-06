# Q3619: Withdrawal replay, idx reuse, or stale marked state

## Question
Can a user get core/contracts/Clearinghouse.sol / rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions) to honor the same withdrawal twice, or honor two semantically different withdrawals under the same replay-protection state, by exploiting idx handling, queue ordering, or state updates?

## Target
- File/function: core/contracts/Clearinghouse.sol / rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions)
- Entrypoint: User submits a signed withdrawal, transfer, liquidation, or settlement action that EndpointTx routes into Clearinghouse.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Replay the same withdrawal under changed sendTo, amount, or transaction bytes and compare markedIdxs, minIdx, and downstream transfer behavior.
- Invariant to test: Each withdrawal request must consume exactly one unique replay-protection slot and must pay out at most once.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through withdrawal replay or double-claim.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
