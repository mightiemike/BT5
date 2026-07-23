### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. If the pool admin adds the router to the allowlist (the only way to permit router-based swaps at all), every user — including those explicitly excluded — can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist: [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the pool call: [2](#0-1) 

which is then forwarded verbatim to `_beforeSwap` → `ExtensionCalling._callExtensionsInOrder`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` seen by the pool is the **router contract address**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** on allowlist | All router-based swaps revert, even for individually allowlisted users |
| Router **on** allowlist | Every user — including those explicitly excluded — can swap by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that guarantee entirely once the router is added to the allowlist. Any address can execute swaps against the pool by calling `MetricOmmSimpleRouter`, receiving oracle-priced output tokens. This constitutes a direct bypass of a configured access-control boundary with fund-impacting consequences (unauthorized parties drain pool liquidity at oracle prices).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery swap path. Pool admins who want allowlisted users to be able to use the router must add it to the allowlist — a natural and expected operational step. The bypass requires no special privileges, no flash loans, and no multi-transaction setup: a single `exactInputSingle` call from any EOA suffices.

---

### Recommendation

The extension must gate on the **economic actor** (the end-user), not the intermediary. Two approaches:

1. **Pass the originating user through the router.** The router could forward the original `msg.sender` inside `extensionData`, and the extension could decode and verify it. This requires a coordinated change to the router and extension.

2. **Check `sender` only when it is not a known router; otherwise reject.** Pool admins would allowlist individual users and never allowlist the router itself, forcing all router users to call the pool directly. This is operationally restrictive but preserves the invariant.

The root fix is to ensure the actor checked by the extension is the same actor to whom the economic action is attributed — mirroring the `owner == controller` enforcement recommended in the external report.

---

### Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension
  - Pool admin allowlists Alice (address A) and the router (address R)
  - Charlie (address C, not allowlisted) wants to swap

Attack:
  1. Charlie calls MetricOmmSimpleRouter.exactInputSingle({..., extensionData: ""})
  2. Router calls pool.swap(charlie_recipient, ...) — msg.sender to pool = router (R)
  3. Pool calls extension.beforeSwap(R, charlie_recipient, ...)
  4. Extension checks allowedSwapper[pool][R] → true (router is allowlisted)
  5. Swap executes; Charlie receives output tokens

Result:
  Charlie, who is explicitly excluded from the allowlist, successfully swaps
  against the curated pool. The per-user allowlist invariant is broken.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
