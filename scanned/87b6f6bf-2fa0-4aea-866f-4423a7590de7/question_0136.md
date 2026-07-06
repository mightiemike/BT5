# Q136: Cross-contract desync of subaccountIds

## Question
Can a normal user drive core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) so that subaccountIds is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Target the exact moment when core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) mutates subaccountIds and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Storage-backed routing and token transfer helpers must not let a user create credit without assets or bypass sanctions and subaccount recording.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through incorrect token movement or storage bookkeeping.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
