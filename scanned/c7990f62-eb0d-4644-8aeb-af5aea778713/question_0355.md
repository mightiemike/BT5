# Q355: Sender alias or linked-signer confusion

## Question
Can core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) treat msg.sender, signed sender, linked signer, fee owner, builder owner, or recipient-derived address as interchangeable in a way that lets one user spend or settle on behalf of another without fresh authorization?

## Target
- File/function: core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount)
- Entrypoint: User reaches EndpointStorage helpers through deposit, withdrawal, or slow-mode execution paths in Endpoint / EndpointTx.
- Attacker controls: token address, from, to, amount, subaccount bytes, sender address
- Exploit idea: Hold the signed fields constant while varying caller, linked signer, isolated-subaccount mapping, builder ownership, and recipient-derived address interpretation to see whether core/contracts/EndpointStorage.sol / safeTransferFrom(IERC20Base token, address from, uint256 amount) conflates those identities.
- Invariant to test: Storage-backed routing and token transfer helpers must not let a user create credit without assets or bypass sanctions and subaccount recording.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation that mutates the wrong account context.
- Fast validation: Fuzz subaccount byte layouts and token-transfer edge cases around EndpointStorage helper call sites and assert stored mappings remain consistent with balances.
