### Title
`SwapAllowlistExtension` Bypass via Router: Allowlisted Router Grants Unrestricted Swap Access to Any User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (a natural operational step to enable router-mediated swaps for permitted users), every user — including those not individually allowlisted — can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router contract**, so `sender` passed to the extension is the router's address. The allowlist check becomes:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][end_user]
```

If the pool admin allowlists the router — a natural step so that individually-permitted users can trade through the standard periphery — the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). LPs deposit into such a pool with the expectation that only approved parties trade against their liquidity. Once the router is allowlisted, any unpermissioned user can trade against LP positions by routing through `MetricOmmSimpleRouter`. This breaks the LP's access-control assumption and exposes their capital to counterparties they explicitly excluded, constituting a broken core pool invariant with direct LP fund-impact potential.

---

### Likelihood Explanation

The trigger requires two conditions:
1. A pool is deployed with `SwapAllowlistExtension` in its `beforeSwap` order.
2. The pool admin allowlists the router address.

Condition 2 is a natural operational step: any admin who wants allowlisted users to trade through the standard periphery must allowlist the router, since the extension sees the router as the swapper. There is no in-protocol warning or guard preventing this configuration. Once both conditions hold, any unpermissioned user can exploit the bypass with a single router call — no special privileges required.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **economic actor** (the end user), not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated router + extension upgrade.

2. **Check `tx.origin` as a fallback** (weaker, but simple): Replace `sender` with `tx.origin` inside the extension when `sender` is a known router. This is fragile against contract wallets.

3. **Preferred — router-aware allowlist**: Extend the extension to maintain a separate `trustedRouter` set; when `sender` is a trusted router, require the extension to receive the real user address in `extensionData` and check that instead.

At minimum, the `SwapAllowlistExtension` NatSpec and the `MetricOmmSimpleRouter` documentation must warn that allowlisting the router grants unrestricted swap access to all router users.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists the router so that permitted users can trade via periphery
ext.setAllowedToSwap(pool, address(router), true);
// Admin does NOT allowlist attacker
// ext.setAllowedToSwap(pool, attacker, false);  // default: false

// Attacker (not individually allowlisted) calls the router
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    tokenIn:         token0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// ✓ Swap succeeds — allowlist bypassed because router is allowlisted
// allowedSwapper[pool][router] == true, so beforeSwap passes
```

The root cause is that `sender` in `SwapAllowlistExtension.beforeSwap` is the router's address when the swap is router-mediated, so `allowedSwapper[pool][router] = true` grants unrestricted access to all router callers. [7](#0-6) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
