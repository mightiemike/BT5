# Q1991: Reentrancy or stale-state window at this.processSlowModeTransaction(...)

## Question
Can core/contracts/Endpoint.sol / processSlowModeTransaction(address sender, bytes calldata transaction) reach this.processSlowModeTransaction(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/Endpoint.sol / processSlowModeTransaction(address sender, bytes calldata transaction)
- Entrypoint: User calls Endpoint.depositCollateral(...) directly.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Use a callback-capable token or recipient around this.processSlowModeTransaction(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Deposits must only create protocol credit for value actually moved into protocol custody.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use a malicious token or callback-capable recipient to test whether Endpoint state mutates safely around external token movement and delegatecall paths.
