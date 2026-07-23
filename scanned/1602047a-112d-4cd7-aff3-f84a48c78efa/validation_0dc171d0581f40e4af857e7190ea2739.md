### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual end user. If the pool admin allowlists the router to support standard periphery usage, every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument forwarded to every configured extension: [2](#0-1) 

**Step 2 — `MetricOmmSimpleRouter` calls `pool.swap` directly, making itself `msg.sender`.**

All four public router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) call `IMetricOmmPoolActions(pool).swap(...)` with no forwarding of the original caller: [3](#0-2) [4](#0-3) 

The pool therefore sees `msg.sender = router`, and the extension receives `sender = router`.

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [5](#0-4) 

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the router address. The check resolves to `allowedSwapper[pool][router]` — the actual end user's address is never consulted.

**The two broken states:**

| Pool admin configuration | Effect |
|---|---|
| Router is allowlisted (to support standard periphery) | Every user — including those explicitly excluded — can bypass the allowlist by calling the router |
| Router is NOT allowlisted | Even explicitly allowlisted users cannot swap through the router; the standard periphery is broken for the pool |

Neither configuration achieves the intended per-user gating.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves at oracle-derived prices, constituting a direct loss of the pool's intended access policy and potential fund-impacting execution on pools that should have been restricted.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point for swaps. Any user who discovers the allowlist restriction on a direct pool call can trivially re-route through the router. No privileged access, special tokens, or unusual setup is required — only knowledge of the router address and a standard swap call.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the address that initiated the swap and will receive or pay tokens — not the intermediary. Two sound approaches:

1. **Forward the original caller through the router.** Add a `swapper` parameter to the router's swap entry points and pass it as `callbackData` or a dedicated field so the pool can forward it as `sender`. This requires a pool-level interface change.

2. **Check `recipient` instead of `sender` in the extension.** If the pool's design guarantees that `recipient` is always the end user (which it is for `exactInputSingle` and `exactOutputSingle`), the extension can gate on the second argument. However, multi-hop `exactOutput` routes intermediate tokens through the router as recipient, so this is not universally correct either.

3. **Simplest safe fix:** In `SwapAllowlistExtension.beforeSwap`, check **both** `sender` and `recipient` — if either is not allowlisted, revert. This prevents the router from acting as a pass-through for non-allowlisted users.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow standard periphery usage).
  - Pool admin does NOT allowlist attacker address.

Attack:
  1. Attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: attacker,
       ...
     })
  2. Router calls pool.swap(attacker, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives token output.

Result:
  The attacker, who is not on the allowlist, successfully swaps on a
  curated pool. The per-user allowlist is completely bypassed.
``` [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
