### Title
`SwapAllowlistExtension` Checks Router Address as `sender` Instead of the Actual End-User, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates pool swaps by checking the `sender` parameter passed to `beforeSwap` against a per-pool allowlist. Because `sender` is `msg.sender` of the `pool.swap(...)` call, it resolves to the `MetricOmmSimpleRouter`'s address whenever a user routes through the periphery. A pool admin who allowlists the router (the only way to let any user swap through it) inadvertently opens the gate to every user, including those explicitly not on the allowlist.

---

### Finding Description

**Hook plumbing — what `sender` is**

`ExtensionCalling._beforeSwap` encodes and forwards `sender` verbatim to every registered