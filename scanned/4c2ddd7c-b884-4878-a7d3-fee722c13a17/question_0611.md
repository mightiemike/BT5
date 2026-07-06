# Q611: Appendix semantic gap

## Question
Can an attacker supply an appendix value that core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) interprets differently across signing, matching, isolation, fee, or withdrawal logic, so that one component treats the same order or withdrawal as a different instruction than another component?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches BaseWithdrawPool.submitWithdrawal(...) indirectly after Clearinghouse routes a withdrawal to the pool.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Vary every meaningful appendix bit while holding the signed core fields fixed, then diff the behavior of digest construction, order validation, isolation handling, fee logic, and withdrawal resolution.
- Invariant to test: Fast-withdrawal signatures and idx tracking must bind the exact withdrawal semantics being paid out.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation through malformed withdrawal payloads.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.
