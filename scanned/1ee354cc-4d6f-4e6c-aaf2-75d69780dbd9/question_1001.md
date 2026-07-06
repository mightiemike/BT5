# Q1001: Hotspot-driven review path

## Question
Does the implementation detail noted for core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) create a reachable exploit path for an unprivileged attacker: WithdrawCollateralV2 uses feeX18 outside the compact-signature digest.

## Target
- File/function: core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Translate the implementation note into an executable proof path and test whether the noted assumption breaks authorization, accounting, queue semantics, or settlement safety.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.
