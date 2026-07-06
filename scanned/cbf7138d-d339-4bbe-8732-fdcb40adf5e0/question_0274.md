# Q274: Ordering dependency around week progression

## Question
Can an attacker manipulate reachable call order so that core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) observes week progression in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Reorder the same user actions around week progression, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Batch claim execution must not let a user replay, overclaim, or partially corrupt claim state in a way that loses funds.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Assert claimed[week][user] is updated exactly once and cannot be rolled back or bypassed through a crafted multi-proof sequence.
