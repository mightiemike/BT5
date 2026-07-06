# Q3539: Arithmetic edge case in priceX18

## Question
Can attacker-controlled extremes of priceX18 drive core/contracts/Clearinghouse.sol / rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/Clearinghouse.sol / rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Fuzz priceX18 around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/Clearinghouse.sol / rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions) mutates balances and risk state.
- Invariant to test: External asset transfers must not happen in a way that leaves user balances or protocol balances inconsistent after failure or reentrancy.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Use a malicious token and withdrawal receiver to test whether Clearinghouse moves funds before all debits, utilization checks, and health checks are final.
