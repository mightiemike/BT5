### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which resolves to `msg.sender` of the `pool.swap()` call — the router contract — not the actual end user. When a pool admin allowlists the router (the only way to let their users swap through the standard periphery), every unprivileged address can bypass the per-user allowlist by routing through the public `MetricOmmSimpleRouter`.

---

### Finding Description

**Step 1 — How the pool resolves `sender`.**

`MetricOmmPool.swap` is called with `recipient` as its only address parameter. The pool uses `msg.sender` as the `sender` it forwards to `ExtensionCalling._beforeSwap`: [1](#0-0) 

The `sender` argument that reaches every extension is therefore the **immediate caller of `pool.swap`**, not the originating user.

**Step 2 — What the router passes.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` to the pool: [2](#0-1) 

The same holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. In every case the router is the entity the pool records as `sender`.

**Step 3 — What the allowlist actually checks.**

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The check is therefore `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Step 4 — The forced dilemma.**

A pool admin who wants their allowlisted users to be able to use the standard router faces two options, both broken:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert — allowlisted users cannot use the periphery |
| **Allowlist the router** | `allowedSwapper[pool][router] = true` → every address on-chain can swap through the router, allowlist is void |

The second option is the fund-impacting path. It is the natural fix an admin would apply after discovering that their allowlisted users cannot swap.

---

### Impact Explanation

Any unprivileged user can swap on a pool that is intended to be permissioned (KYC-gated, institutional-only, or otherwise restricted) by calling `MetricOmmSimpleRouter.exactInputSingle` or any multi-hop variant. The allowlist guard configured by the pool admin is completely ineffective for all router-mediated swaps once the router is allowlisted. This constitutes an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses the access control the pool admin configured.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` will inevitably discover that their allowlisted users cannot swap through the router and will allowlist the router address to fix it — at which point the guard is permanently bypassed for all users.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor**, not the immediate caller. Two sound approaches:

1. **Check `sender` against the allowlist only when `sender` is not a trusted router; otherwise check the user address embedded in `extensionData`.** Require the router to encode `msg.sender` (the real user) into `extensionData` and have the extension decode and verify it.

2. **Gate on `tx.origin` as a secondary check when `sender` is a known router.** Less clean but avoids extensionData coupling.

The `DepositAllowlistExtension` does not share this exact flaw (it checks `owner`, which the liquidity adder sets to the position owner), but should be audited for the analogous owner-vs-payer separation.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)  // "fix" so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:          pool,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) — msg.sender to pool = router
6. Pool calls _beforeSwap(router, bob, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  ✓
8. Swap executes successfully for Bob despite Bob not being allowlisted.
``` [4](#0-3) [5](#0-4) [1](#0-0)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
