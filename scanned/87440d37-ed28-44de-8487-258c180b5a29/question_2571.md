# Q2571: Failure-handling mismatch after clearinghouseLiq.delegatecall(...)

## Question
Can attacker-controlled failure behavior around clearinghouseLiq.delegatecall(...) leave core/contracts/Clearinghouse.sol / delistProduct(bytes calldata transaction) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/Clearinghouse.sol / delistProduct(bytes calldata transaction)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Force clearinghouseLiq.delegatecall(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: External asset transfers must not happen in a way that leaves user balances or protocol balances inconsistent after failure or reentrancy.
- Expected HackenProof impact: Critical/High: stealing or loss of funds from the exchange, withdraw pool, or insurance accounting.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
