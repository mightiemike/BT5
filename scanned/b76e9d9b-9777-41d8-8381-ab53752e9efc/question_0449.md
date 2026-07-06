# Q449: Shared key, index, or mapping-collision confusion

## Question
Can attacker-controlled identifiers reaching core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) collide in a shared mapping, bitmap, queue index, digest bucket, or derived storage key so that one user’s action overwrites, unlocks, or consumes another user’s state?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Search for every derived storage key, bitmap slot, queue index, digest map, or hash bucket touched by core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs); then try to construct two economically different actions that land on the same storage location.
- Invariant to test: Distinct users, subaccounts, orders, withdrawals, products, and queue items must never alias the same live state slot unless they are intentionally the same object.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or loss of funds through state-key collision.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
