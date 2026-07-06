# Q2291: Withdrawal replay, idx reuse, or stale marked state

## Question
Can a user get core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) to honor the same withdrawal twice, or honor two semantically different withdrawals under the same replay-protection state, by exploiting idx handling, queue ordering, or state updates?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Replay the same withdrawal under changed sendTo, amount, or transaction bytes and compare markedIdxs, minIdx, and downstream transfer behavior.
- Invariant to test: Each withdrawal request must consume exactly one unique replay-protection slot and must pay out at most once.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through withdrawal replay or double-claim.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.
