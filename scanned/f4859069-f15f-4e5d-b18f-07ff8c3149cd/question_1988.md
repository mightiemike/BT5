# Q1988: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx)
- Entrypoint: User calls BaseWithdrawPool.submitFastWithdrawal(...) directly with a transaction blob and signature set.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/BaseWithdrawPool.sol / submitWithdrawal(IERC20Base token, address sendTo, uint128 amount, uint64 idx), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Withdrawals must execute at most once per unique request and must not exceed the user’s withdrawable amount.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.
