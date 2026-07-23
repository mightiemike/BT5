### Title
SwapAllowlistExtension Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the immediate `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the restriction by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever address called `pool.swap()`.

`MetricOmmPool.swap` passes `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← sender = whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The router stores the actual user's address only in transient storage for callback settlement — it is **never forwarded to `pool.swap()`** and therefore never reaches the extension. The extension sees `sender = router` for every router-mediated swap, regardless of who called the router.

**The invariant break:** If the pool admin allowlists the router address (required for any allowlisted user to use the router), `allowedSwapper[pool][router] = true` causes the check to pass for every caller of the router, including non-allowlisted users.

The `generate_scanned_questions.py` audit target explicitly identifies this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist by calling `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a restricted pool. The pool admin cannot simultaneously:
- Allow allowlisted users to use the public router, **and**
- Prevent non-allowlisted users from using the same router.

Allowlisting the router is an all-or-nothing decision that collapses the per-user restriction to a per-router restriction. Unauthorized users gain full swap access to a pool intended to be restricted, which can result in unauthorized trading, regulatory violations, or exploitation of pool-specific pricing assumptions that depend on a controlled counterparty set.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. However, this is the expected operational step whenever allowlisted users are expected to interact via the standard periphery. A pool admin who configures `SwapAllowlistExtension` and also wants router support will naturally allowlist the router, unknowingly opening the gate to all users.

---

### Recommendation

The extension must gate on the **end user's identity**, not the immediate caller. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` only for direct pool calls; require a trusted forwarder pattern for router calls:** The extension distinguishes between direct swappers and router-mediated swaps and applies different identity resolution logic.

Until fixed, pools that require per-user swap restrictions should not allowlist the router and should document that router-mediated swaps are incompatible with `SwapAllowlistExtension`.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allow userA to use the router
4. Non-allowlisted userB calls:
       router.exactInputSingle({pool: restrictedPool, ...})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. beforeSwap receives sender = router.
7. allowedSwapper[pool][router] == true → check passes.
8. userB's swap executes in the restricted pool.
```

**Affected files:**
- `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` — line 37: `allowedSwapper[msg.sender][sender]` checks router, not end user
- `metric-periphery/contracts/MetricOmmSimpleRouter.sol` — lines 71–80: actual user address is never forwarded to `pool.swap()`
- `metric-core/contracts/MetricOmmPool.sol` — line 231: `msg.sender` (router) is passed as `sender` to extensions