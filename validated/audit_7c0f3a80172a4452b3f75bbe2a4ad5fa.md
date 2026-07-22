### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the per-address allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is called by the pool with `sender = msg.sender` (whoever called `pool.swap()`):

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

The extension then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not the actual end-user.

**Two broken states result:**

1. **Bypass (primary impact):** The pool admin allowlists the router so that allowlisted users can swap via the router. Because the check is on the router address, every user — including those not individually allowlisted — can call `router.exactInputSingle()` and pass the guard. The per-address allowlist is completely nullified for all router-mediated swaps.

2. **Lockout (secondary impact):** If the pool admin does not allowlist the router, individually allowlisted users are blocked from using the router entirely, breaking the expected swap flow for those users.

---

### Impact Explanation

The `SwapAllowlistExtension` is the pool admin's mechanism to restrict which addresses may trade in a pool — for example, to limit trading to KYC-verified counterparties, specific market makers, or institutional participants. When the router is allowlisted (the only way to enable router-mediated swaps), the guard collapses to "anyone who calls the router can swap," defeating the allowlist entirely. Non-allowlisted users can trade in a pool that was designed to exclude them, exposing LP capital to adverse selection from actors the pool admin explicitly intended to block. This is an admin-boundary break: a security boundary set by the pool admin is bypassed by an unprivileged path (the public router).

---

### Likelihood Explanation

The trigger requires no special privilege. Any user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`. The router is a public, permissionless contract. The bypass is reachable on every swap routed through the periphery against any pool that has `SwapAllowlistExtension` configured and the router allowlisted.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass the real swapper via `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check both caller and end-user:** Require that either the direct `sender` or a verified end-user address embedded in `extensionData` is allowlisted, and that the direct `sender` is a trusted router registered with the factory.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // enables router swaps
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually allowlisted
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob, bypassing the per-address allowlist
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
