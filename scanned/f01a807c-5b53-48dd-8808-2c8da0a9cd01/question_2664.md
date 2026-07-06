# Q2664: Subaccount authorization drift across derived identities

## Question
Can an unprivileged user drive core/contracts/Endpoint.sol / submitTransactionsChecked(uint64 idx, bytes[] calldata transactions, bytes32 e, bytes32 s, uint8 signerBitmask) with one sender or subaccount identity at validation time but a different effective sender or subaccount identity at execution time, causing state to mutate for the wrong account?

## Target
- File/function: core/contracts/Endpoint.sol / submitTransactionsChecked(uint64 idx, bytes[] calldata transactions, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User queues a transaction through Endpoint.submitSlowModeTransaction(...) and later executes it through Endpoint.executeSlowModeTransaction(...).
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Trace every conversion between address, bytes32 sender, linked signer, parent subaccount, isolated subaccount, and derived recipient around core/contracts/Endpoint.sol / submitTransactionsChecked(uint64 idx, bytes[] calldata transactions, bytes32 e, bytes32 s, uint8 signerBitmask); then try to keep validation attached to one identity while execution lands on another.
- Invariant to test: Only the exact authorized account, subaccount, or linked signer should be able to mutate that account’s balances, positions, orders, or withdrawals.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
