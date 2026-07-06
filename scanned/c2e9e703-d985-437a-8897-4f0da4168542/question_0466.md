# Q466: Stale or double-applied claimed

## Question
Can attacker-controlled sequencing make core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) consume stale claimed or apply the same claimed transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale claimed before all related state is finalized.
- Invariant to test: Batch claim execution must not let a user replay, overclaim, or partially corrupt claim state in a way that loses funds.
- Expected HackenProof impact: Medium/Low: logic attack causing incorrect airdrop accounting with a runnable proof path.
- Fast validation: Assert claimed[week][user] is updated exactly once and cannot be rolled back or bypassed through a crafted multi-proof sequence.
