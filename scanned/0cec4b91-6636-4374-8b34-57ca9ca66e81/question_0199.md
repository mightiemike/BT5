# Q199: First-use, zero-state, or empty-state boundary bug

## Question
Can the first interaction with a fresh nonce, empty balance, empty mapping slot, uninitialized queue entry, first fill, first claim, or first isolated-subaccount state around core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) behave differently enough from later interactions to create an exploitable accounting or authorization gap?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Compare the exact first-use path against the steady-state path for core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount), especially around zero balances, empty mappings, untouched fee state, empty arrays, and first-time sender or subaccount initialization.
- Invariant to test: Storage-backed routing and token transfer helpers must not let a user create credit without assets or bypass sanctions and subaccount recording.
- Expected HackenProof impact: Critical/High: logic attack or unauthorized transaction through inconsistent zero-state handling.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
