# Q1607: Reentrancy or stale-state window at clearinghouse.withdrawCollateral(...)

## Question
Can core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction) reach clearinghouse.withdrawCollateral(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Use a callback-capable token or recipient around clearinghouse.withdrawCollateral(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Fuzz digest-bound fields versus decoded fields and assert the same signature cannot authorize two economically different actions.
