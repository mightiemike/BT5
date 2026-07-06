# Q1238: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Deposits must only create protocol credit for value actually moved into protocol custody.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
