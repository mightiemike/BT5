### Title
DepositAllowlistExtension Checks `owner` (Share Recipient) Instead of `sender` (Payer), Allowing Any User to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` argument (the address that will receive LP shares) rather than the `sender` argument (the address that actually calls `addLiquidity` and pays via the swap callback). Because `MetricOmmPool.addLiquidity` lets any caller supply an arbitrary `owner`, an unprivileged user can bypass the deposit allowlist by naming any already-allowlisted address as `owner`, paying the tokens themselves, and then recovering the LP position through that address.

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner`