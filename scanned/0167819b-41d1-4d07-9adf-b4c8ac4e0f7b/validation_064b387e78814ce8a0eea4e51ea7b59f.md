### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating user. A pool admin who allowlists the router — a natural step to let their allowlisted users access the router — inadvertently opens the gate to every user who routes through that same router, including non-allowlisted ones.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict swaps to a configured set of addresses per pool. Its `beforeSwap` hook receives `sender` as the first argument, which the pool sets to `msg.sender` of the `pool.swap()` call. [1](#0-0) 

The guard logic is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router calls `pool.swap()` directly: [2](#0-1) 

This makes `sender = router address`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool dispatches `sender` to the extension through `ExtensionCalling._beforeSwap`: [3](#0-2) 

There is no mechanism by which the router forwards the originating user's address into the `sender` slot; the router simply calls `pool.swap()` and the pool records `msg.sender` (the router) as `sender`.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users (e.g., `alice`): `setAllowedToSwap(pool, alice, true)`.
2. Pool admin also allowlists the router so that `alice` can use the router: `setAllowedToSwap(pool, router, true)`.
3. `charlie` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amountSpecified, priceLimitX64, "", extensionData)`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. `charlie`'s swap executes successfully despite not being allowlisted. [4](#0-3) 

The `setAllowedToSwap` admin setter: [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can swap on a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The access-control invariant the extension is designed to enforce — "only allowlisted addresses may swap" — is fully broken for any pool whose admin has allowlisted the router. Pools using the allowlist for regulatory compliance, institutional-only access, or KYC gating are silently open to all router users. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool-admin-configured guard.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is the natural and expected action for any allowlisted pool that wants its permitted users to be able to use the standard periphery router. The bypass is then trivially reachable by any user with no special permissions or capital requirements beyond a normal swap.

---

### Recommendation

The `SwapAllowlistExtension` must check the originating user, not the direct caller. Two concrete options:

1. **Router-forwarded identity via `extensionData`**: The

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
