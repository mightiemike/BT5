### Title
Wrong-actor binding in `DepositAllowlistExtension.beforeAddLiquidity` allows unauthorized depositors to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position recipient) against the allowlist instead of the `sender` (the actual depositor who pays tokens). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unauthorized