# Q64: Callback-driven post-state ordering bug

## Question
Can a token, recipient, or helper callback interacting around core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) observe a half-updated state and force a second reachable action before all balances, replay markers, fee state, or claim state are finalized?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Use malicious token hooks, recipient fallback logic, helper contracts, or chained calls around core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs); then verify whether any second action can read or exploit intermediate state before finalization.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: reentrancy or transaction manipulation through externally observable intermediate state.
- Fast validation: Assert claimed[week][user] is updated exactly once and cannot be rolled back or bypassed through a crafted multi-proof sequence.
