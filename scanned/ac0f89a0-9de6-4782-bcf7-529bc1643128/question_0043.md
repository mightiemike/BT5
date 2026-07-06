# Q43: Arithmetic edge case in week

## Question
Can attacker-controlled extremes of week drive core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Fuzz week around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) mutates balances and risk state.
- Invariant to test: Batch claim execution must not let a user replay, overclaim, or partially corrupt claim state in a way that loses funds.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Assert claimed[week][user] is updated exactly once and cannot be rolled back or bypassed through a crafted multi-proof sequence.
