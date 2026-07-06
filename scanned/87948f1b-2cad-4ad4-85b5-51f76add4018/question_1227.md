# Q1227: Failure-handling mismatch after verifier.requireValidSignature(...)

## Question
Can attacker-controlled failure behavior around verifier.requireValidSignature(...) leave core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory) in a partially-applied state where assets moved, but balances, fees, or replay markers did not settle consistently?

## Target
- File/function: core/contracts/Endpoint.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory)
- Entrypoint: User calls Endpoint.depositCollateralWithReferral(...) with a crafted subaccount or token amount.
- Attacker controls: subaccountName, subaccount, productId, amount, transaction calldata, queue timing, slow-mode ordering, recipient contract behavior
- Exploit idea: Force verifier.requireValidSignature(...) to revert, return false, consume abnormal gas, or partially succeed and compare protocol state before and after the revert path.
- Invariant to test: User-controlled calldata must not cause Endpoint to delegate into EndpointTx in a way that mutates unauthorized state.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through incorrect deposit, queue, or withdrawal routing.
- Fast validation: Write a Hardhat test that deposits through Endpoint and compare actual ERC20 balances against credited balances and queued slow-mode deposits.
