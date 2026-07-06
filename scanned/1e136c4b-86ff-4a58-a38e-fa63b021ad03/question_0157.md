# Q157: Failure-handling mismatch after token.safeTransfer(...)

## Question
Can attacker-controlled failure behavior around token.safeTransfer(...) leave core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Force token.safeTransfer(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: Storage-backed routing and token transfer helpers must not let a user create credit without assets or bypass sanctions and subaccount recording.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through incorrect token movement or storage bookkeeping.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
