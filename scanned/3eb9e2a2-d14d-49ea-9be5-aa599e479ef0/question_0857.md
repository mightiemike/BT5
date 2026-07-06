# Q857: Cross-contract desync of sequencerFee

## Question
Can a normal user drive core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) so that sequencerFee is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Target the exact moment when core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) mutates sequencerFee and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Queueing, replay protection, and signer linkage must not let a user mutate another account or reuse stale authorization.
- Expected HackenProof impact: Critical/High: stealing or loss of funds by replaying or reshaping a signed endpoint action.
- Fast validation: Fuzz digest-bound fields versus decoded fields and assert the same signature cannot authorize two economically different actions.
