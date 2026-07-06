# Q127: Double-claim or batch-claim state corruption

## Question
Can a user call core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) with duplicated or adversarially ordered claim data so that claim state updates for one element do not prevent a second economically equivalent payout in the same or later transaction?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Use duplicate entries, duplicate weeks, repeated proofs, and same-leaf multi-call sequences while checking whether the claimed mapping blocks every equivalent payout path.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through duplicate or manipulated claims.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
