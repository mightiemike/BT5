### Title
SwapAllowlistExtension gates the router address instead of the original user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the original user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the gate to every user on the internet, because the allowlist check never sees the original user's address.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so `sender` delivered to `beforeSwap` is the router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, never touching the original user's address.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for allowlisted users |
| **Allowlist the router** | Every user on the internet can bypass the allowlist by calling the router |

There is no configuration that correctly gates individual users while still permitting router access.

The structural parallel to the MooniswapGovernance bug is exact: in that case `balanceTo` was computed with `from != address(0)` instead of `to != address(0)`, substituting the wrong identity into a critical guard and silently zeroing the value that should have been non-zero. Here, `sender` substitutes the router's identity for the user's identity in the allowlist lookup, silently passing a check that should have blocked the caller.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) provides **no effective restriction** once the router is allowlisted. Any anonymous user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router and the allowlist hook will pass because it sees the router, not the user. This constitutes a complete bypass of the pool's access-control layer, allowing unauthorized parties to drain liquidity at oracle prices, extract LP value, or execute trades the pool admin explicitly intended to prohibit.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior interaction is required. Any EOA or contract can call it. The bypass is reachable in a single transaction with no setup beyond knowing the pool address.

---

### Recommendation

The extension must receive the **original user's address**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` already stores the payer in transient storage (`_getPayer()`). The pool interface could be extended to carry an `originator` field, or the router could encode the original user in `extensionData` and the extension could decode it. The cleanest fix is to have the pool pass a separate `originator` field to hooks that routers populate.

2. **Short-term mitigation**: Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level (e.g., revert pool creation that configures both).

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only approved user
  allowedSwapper[pool][router] = true         // admin enables router so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  → check: allowedSwapper[pool][router] == true  ✓ passes
  → bob's swap executes against the restricted pool
```

Bob, who is not on the allowlist, successfully swaps because the guard checked the router's address (`allowedSwapper[pool][router] = true`) rather than Bob's address (`allowedSwapper[pool][bob] = false`). [3](#0-2) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
