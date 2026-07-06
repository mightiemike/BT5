# Q3718: Subaccount authorization drift across derived identities

## Question
Can an unprivileged user drive core/contracts/Clearinghouse.sol / settlePnl(bytes calldata transaction) with one sender or subaccount identity at validation time but a different effective sender or subaccount identity at execution time, causing state to mutate for the wrong account?

## Target
- File/function: core/contracts/Clearinghouse.sol / settlePnl(bytes calldata transaction)
- Entrypoint: User submits a signed NLP, transferQuote, or settlePnl flow that eventually mutates clearinghouse state.
- Attacker controls: sender, recipient, subaccount, productId, quoteId, amount, priceX18, idx, sendTo, spreads-linked product IDs
- Exploit idea: Trace every conversion between address, bytes32 sender, linked signer, parent subaccount, isolated subaccount, and derived recipient around core/contracts/Clearinghouse.sol / settlePnl(bytes calldata transaction); then try to keep validation attached to one identity while execution lands on another.
- Invariant to test: Only the exact authorized account, subaccount, or linked signer should be able to mutate that account’s balances, positions, orders, or withdrawals.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat invariant that tracks ERC20 balances, withdraw-pool balances, insurance, and engine balances through deposit/withdraw/settle/liquidate sequences.
