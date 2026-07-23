### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing non-allowlisted callers to deposit into curated pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually pays for the tokens via the swap callback) and checks only the caller-supplied `owner` argument (the position recipient). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity(allowlisted_address, …)`, attributing the position to an allowlisted address while they supply the tokens.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the before-hook with `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlist