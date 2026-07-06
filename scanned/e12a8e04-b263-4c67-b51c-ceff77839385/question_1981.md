# Q1981: Double-claim or batch-claim state corruption

## Question
Can a user call core/contracts/Clearinghouse.sol / claimSequencerFees(int128[] calldata fees) with duplicated or adversarially ordered claim data so that claim state updates for one element do not prevent a second economically equivalent payout in the same or later transaction?

## Target
- File/function: core/contracts/Clearinghouse.sol / claimSequencerFees(int128[] calldata fees)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Use duplicate entries, duplicate weeks, repeated proofs, and same-leaf multi-call sequences while checking whether the claimed mapping blocks every equivalent payout path.
- Invariant to test: A user must not withdraw, transfer, mint, burn, or settle against collateral or equity they do not actually own.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
