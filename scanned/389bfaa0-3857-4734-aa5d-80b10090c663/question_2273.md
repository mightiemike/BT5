# Q2273: Reentrancy or stale-state window at endpointTx.delegatecall(...)

## Question
Can core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18) reach endpointTx.delegatecall(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/Endpoint.sol / setInitialPrice(uint32 productId, int128 initialPriceX18)
- Entrypoint: User queues a transaction through Endpoint.submitSlowModeTransaction(...) and later executes it through Endpoint.executeSlowModeTransaction(...).
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Use a callback-capable token or recipient around endpointTx.delegatecall(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Deposits must only create protocol credit for value actually moved into protocol custody.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Use a malicious token or callback-capable recipient to test whether Endpoint state mutates safely around external token movement and delegatecall paths.
