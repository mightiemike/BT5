# Q2012: Nonce consume mismatch across fail, cancel, or alternate path

## Question
Can the same nonce, idx, or fill marker around core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) be left unused on one path but considered consumed on another, allowing replay on the favorable branch or grief-free reuse after partial execution?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Exercise success, revert, partial-fill, cancel, and alternate-recipient branches around core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx); then compare whether replay protection is consumed consistently across all economically equivalent paths.
- Invariant to test: Replay protection must be consumed exactly once for each signed or queued instruction, regardless of which reachable execution branch is taken.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or transaction manipulation through inconsistent nonce consumption.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.
