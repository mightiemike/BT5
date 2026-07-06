# Q31: Chain, domain, or contract binding gap

## Question
Can authorization accepted by core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) be replayed across a different chain, proxy implementation, verifying contract, or helper context because the signed domain does not fully match the execution domain?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Recreate the same signed payload under alternate chainId, proxy, helper, verifying-contract, or domain-separator contexts and check whether core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) still accepts it for a different live execution surface.
- Invariant to test: Signed actions must bind the exact live Nado execution domain and must not survive a change in chain, contract, proxy, or helper context.
- Expected HackenProof impact: Critical/High: replay or unauthorized transaction through insufficient domain separation.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
