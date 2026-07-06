# Q169: Failure-handling mismatch after SafeERC20.safeTransfer(...)

## Question
Can attacker-controlled failure behavior around SafeERC20.safeTransfer(...) leave core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/Airdrop.sol / claim(ClaimProof[] calldata claimProofs)
- Entrypoint: User calls Airdrop.claim(...) with one or more claim proofs.
- Attacker controls: claimProofs array, week, totalAmount, proof ordering, duplicate proof entries
- Exploit idea: Force SafeERC20.safeTransfer(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: Each address should claim each merkle-root week at most once for the exact amount committed by the merkle root.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through duplicate or manipulated claims.
- Fast validation: Write a Hardhat merkle-claim test that duplicates entries, reorders weeks, and mutates totalAmount/proof pairs in the same batch.
