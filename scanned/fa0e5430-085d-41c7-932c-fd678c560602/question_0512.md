# Q512: Stale or double-applied aggregatePubkey

## Question
Can attacker-controlled sequencing make core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) consume stale aggregatePubkey or apply the same aggregatePubkey transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User or relayer submits fast-withdrawal signatures that WithdrawPool verifies through Verifier.requireValidTxSignatures(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale aggregatePubkey before all related state is finalized.
- Invariant to test: Batch or fast-withdrawal signatures must not be reusable across chain IDs, idx values, or semantically different transactions.
- Expected HackenProof impact: Critical/High: transaction manipulation or replay of signed orders, withdrawals, or liquidations.
- Fast validation: Build a withdrawal replay test that varies chain ID, idx, sendTo, and transaction bytes around requireValidTxSignatures(...).
