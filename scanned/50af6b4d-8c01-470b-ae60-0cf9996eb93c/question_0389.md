# Q389: Stale or double-applied linkedSigners

## Question
Can attacker-controlled sequencing make core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) consume stale linkedSigners or apply the same linkedSigners transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale linkedSigners before all related state is finalized.
- Invariant to test: Storage-backed routing and token transfer helpers must not let a user create credit without assets or bypass sanctions and subaccount recording.
- Expected HackenProof impact: Critical/High: unauthorized transaction side effects through bad subaccount recording or nonce storage assumptions.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
