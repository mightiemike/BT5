# Q1801: Subaccount authorization drift across derived identities

## Question
Can an unprivileged user drive core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures) with one sender or subaccount identity at validation time but a different effective sender or subaccount identity at execution time, causing state to mutate for the wrong account?

## Target
- File/function: core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures)
- Entrypoint: User interacts with WithdrawPool through normal withdrawal and fast-withdrawal flows.
- Attacker controls: idx, transaction bytes, signatures, productId, sendTo, amount, fee payer, recipient contract behavior
- Exploit idea: Trace every conversion between address, bytes32 sender, linked signer, parent subaccount, isolated subaccount, and derived recipient around core/contracts/BaseWithdrawPool.sol / submitFastWithdrawal(uint64 idx, bytes calldata transaction, bytes[] calldata signatures); then try to keep validation attached to one identity while execution lands on another.
- Invariant to test: Only the exact authorized account, subaccount, or linked signer should be able to mutate that account’s balances, positions, orders, or withdrawals.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test around submitFastWithdrawal(...) that replays the same idx, mutates one field at a time, and uses a malicious recipient contract.
