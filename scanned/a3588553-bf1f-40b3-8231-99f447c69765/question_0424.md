# Q424: Partial batch progress without full rollback

## Question
Can a loop, queue, or multi-step batch around core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction) make economic progress on early items even though a later item fails, leaving fill state, claim state, fees, or balances inconsistent with an all-or-nothing user assumption?

## Target
- File/function: core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Construct a mixed-validity batch or queue sequence through core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction), force one later element to fail, and compare whether earlier state changes remain committed in a way that can be exploited or replayed.
- Invariant to test: Batched or queued user actions must either preserve consistent partial-progress rules or prevent attackers from extracting value from early-commit and late-fail combinations.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through inconsistent partial progress handling.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.
