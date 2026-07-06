# Q724: Cross-contract desync of subaccountIds

## Question
Can a normal user drive core/contracts/Endpoint.sol / depositCollateral(bytes12 subaccountName, uint32 productId, uint128 amount) so that subaccountIds is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateral(bytes12 subaccountName, uint32 productId, uint128 amount)
- Entrypoint: User calls Endpoint.depositCollateral(...) directly.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Target the exact moment when core/contracts/Endpoint.sol / depositCollateral(bytes12 subaccountName, uint32 productId, uint128 amount) mutates subaccountIds and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: User-controlled calldata must not cause Endpoint to delegate into EndpointTx in a way that mutates unauthorized state.
- Expected HackenProof impact: Critical/High: reordering bug that breaks intended batch or slow-mode semantics and causes wrong settlement or fund movement.
- Fast validation: Use a malicious token or callback-capable recipient to test whether Endpoint state mutates safely around external token movement and delegatecall paths.
