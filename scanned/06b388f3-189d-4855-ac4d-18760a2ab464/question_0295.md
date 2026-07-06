# Q295: Parallel-array or paired-input mismatch

## Question
Can attacker-controlled arrays, paired structs, or transaction bundles reaching core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) become length-mismatched, order-mismatched, or semantically mismatched so that one element’s validation is applied to another element’s execution?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Fuzz bundle size, order, duplicate elements, and cross-array alignment around core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs); then check whether validation, pricing, or balance application ever shifts from one logical item to another.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: unauthorized transaction or logic attack through mismatched batched semantics.
- Fast validation: Assert claimed[week][user] is updated exactly once and cannot be rolled back or bypassed through a crafted multi-proof sequence.
