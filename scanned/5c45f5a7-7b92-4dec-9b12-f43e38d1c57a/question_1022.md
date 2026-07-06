# Q1022: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/ContractOwner.sol / wrapVaultAsset(bytes32 subaccount, uint32 productId) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/ContractOwner.sol / wrapVaultAsset(bytes32 subaccount, uint32 productId)
- Entrypoint: User calls non-owner ContractOwner helper flows such as creditDepositV1(...), wrapVaultAsset(...), createDirectDepositV1(...), or replaceUsdcEWithUsdc(...).
- Attacker controls: subaccount, productId, helper call timing, ERC4626 preview output, token balances held by the direct-deposit helper
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/ContractOwner.sol / wrapVaultAsset(bytes32 subaccount, uint32 productId); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Write a Hardhat test that calls the public helper functions against another user’s subaccount and assert no unauthorized asset movement or helper-state mutation occurs.
