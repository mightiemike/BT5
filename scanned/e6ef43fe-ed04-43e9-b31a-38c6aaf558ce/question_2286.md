# Q2286: Reentrancy or stale-state window at perpEngine.socializeSubaccount(...)

## Question
Can core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn) reach perpEngine.socializeSubaccount(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/ClearinghouseLiq.sol / liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
- Entrypoint: User manipulates account state through trading, settlement, or transfer flows before triggering liquidation or finalization.
- Attacker controls: liquidator subaccount, liquidatee subaccount, productId, isEncodedSpread, amount, nonce, quote balance state, spread composition
- Exploit idea: Use a callback-capable token or recipient around perpEngine.socializeSubaccount(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Only liquidatable accounts should be liquidated, and liquidation must not seize more than allowed or manufacture insurance/funding value.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Trace delegatecall storage writes in liquidation and assert no path mutates unrelated storage slots or skips required post-checks.
