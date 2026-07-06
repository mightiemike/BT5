# Q2390: Subaccount authorization drift across derived identities

## Question
Can an unprivileged user drive core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn) with one sender or subaccount identity at validation time but a different effective sender or subaccount identity at execution time, causing state to mutate for the wrong account?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Trace every conversion between address, bytes32 sender, linked signer, parent subaccount, isolated subaccount, and derived recipient around core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn); then try to keep validation attached to one identity while execution lands on another.
- Invariant to test: Only the exact authorized account, subaccount, or linked signer should be able to mutate that account’s balances, positions, orders, or withdrawals.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat scenario that sets up healthy and unhealthy accounts, then fuzz liquidation amounts, spread encodings, and settlement ordering to assert exact seize bounds.
