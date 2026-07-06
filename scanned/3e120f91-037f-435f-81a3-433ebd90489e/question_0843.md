# Q843: Residual state after close, zero, or cleanup path

## Question
Can core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral) leave behind residual balance, fee, nonce, fill, queue, isolation, or bitmap state after a position or request is supposedly closed, zeroed, or cleared, allowing the attacker to reuse that leftover state later?

## Target
- File/function: core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral)
- Entrypoint: User sends native tokens to the DirectDepositV1 receive() path or routes ERC4626 wrapping through ContractOwner helper flows.
- Attacker controls: subaccount, productIds list chosen by the live spot engine, token behavior, balance held by the helper, wrappedNative behavior, timing of creditDeposit()
- Exploit idea: Drive the relevant object to an exact zero, fully-filled, fully-withdrawn, fully-claimed, or closed state through core/contracts/DirectDepositV1.sol / depositCollateralWithReferral(bytes32 subaccount, uint32 productId, uint128 amount, string memory referral); then probe whether any residual storage can still be consumed or interpreted as active state.
- Invariant to test: Once an order, withdrawal, claim, position, or isolated state is fully closed, no residual state should remain that can be replayed or monetized later.
- Expected HackenProof impact: Critical/High: replay, double-claim, or logic attack through stale residual state.
- Fast validation: Test repeated creditDeposit() and receive() flows around wrappedNative and ERC4626 wrapping to assert no stale approvals or double-credit paths exist.
