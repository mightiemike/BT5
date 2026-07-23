### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. If the router is allowlisted (required for any router-mediated swap to succeed), every user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

At this point `pool.msg.sender` = **router address**, so the extension receives `sender` = router, not the actual user. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for pool admins:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user can bypass the allowlist by routing through the router |
| Do not allowlist the router | No user can use the router; all router-mediated swaps revert |

There is no configuration that allows specific users to swap via the router while blocking others.

The same structural problem applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router itself) as the payer: [5](#0-4) 

---

### Impact Explanation

The swap allowlist guard is completely neutralized for router-mediated swaps. Any unprivileged user can trade on a pool that was configured to be restricted (e.g., institutional-only, KYC-gated, or partner-only). This breaks the core access-control invariant of the extension and allows unauthorized parties to consume pool liquidity at oracle-anchored prices, which may be more favorable than market prices.

---

### Likelihood Explanation

Exploitation requires only that the router is allowlisted on the target pool — a necessary condition for any legitimate user to use the router. The router is a public, permissionless contract. No special privileges, flash loans, or multi-transaction setup are needed. Any user who can call the router can bypass the allowlist in a single transaction.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Recipient-based gating**: Gate on `recipient` instead of (or in addition to) `sender`, since the recipient is the address that receives the output tokens and is harder to spoof without economic loss.

3. **Separate router allowlist**: Maintain a separate allowlist for routers and require that the router itself enforces per-user allowlist checks before calling the pool.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)       // Alice is allowed
  - Admin calls setAllowedToSwap(pool, router, true)      // Router must be allowed for Alice to use it
  - Bob is NOT allowlisted

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes successfully — Bob swaps on a pool he was not authorized to access
```

The root cause is at: [6](#0-5) 

combined with the router's direct `pool.swap()` call that substitutes the router address for the user: [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
