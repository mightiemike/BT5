# Q381: Rounding leak through claimProofs array length

## Question
Can repeated user-controlled updates around claimProofs array length make core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving claimProofs array length; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
