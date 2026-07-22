### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. The pool always passes `msg.sender` of the `pool.swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The extension therefore checks whether the router is allowlisted, not whether the individual user is allowlisted. Any user who routes through the public router on a pool that has allowlisted the router address bypasses the per-user access gate entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` inside the extension is the pool (enforced by `onlyPool`). `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original caller's identity:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← original user address is NOT forwarded
    );
```

The router stores `msg.sender` only in the transient callback context for payment settlement; it is never passed to the pool as the swapper identity. The pool therefore sees the router as `msg.sender`, and the extension checks `allowedSwapper[pool][router]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (necessary for any allowlisted user to use the router).
2. A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting that pool.
3. The pool receives `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap proceeds.
4. The non-allowlisted user has successfully swapped on a pool that was supposed to be restricted to specific addresses.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, including the recursive callback path in `_exactOutputIterateCallback` where intermediate hops call `pool.swap(msg.sender, ...)` with `msg.sender` being the router itself.

### Impact Explanation

A pool admin cannot simultaneously (a) allow allowlisted users to use the public router and (b) block non-allowlisted users from using the same router. Once the router is allowlisted, the per-user gate is fully open to any caller who routes through it. The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool" but this invariant is broken for all router-mediated swaps. Non-allowlisted users gain unrestricted swap access to curated pools, defeating the curation policy and any downstream compliance, fee-tier, or LP-protection intent behind the allowlist.

### Likelihood Explanation

The router is the primary supported swap entrypoint in the periphery. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which immediately opens the bypass to all users. The trigger requires no special privileges, no malicious setup, and no non-standard tokens — only a standard call to the public router.

### Recommendation

The `sender` identity forwarded to extensions must reflect the **original economic actor**, not the intermediary contract. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (e.g., as a leading 20-byte prefix) and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Extension-side**: Add an explicit `originalSender` field to the `beforeSwap` interface, populated by the pool from a transient context set by the router before calling `pool.swap()`, analogous to how the router already stores the payer in transient storage for the callback.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][router] = true      // admin allowlists router so Alice can use it
  allowedSwapper[pool][Alice]  = true      // Alice is an approved user
  allowedSwapper[pool][Bob]    = false     // Bob is NOT approved

Attack:
  Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for Bob despite Bob not being allowlisted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
