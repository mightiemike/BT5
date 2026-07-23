### Title
SwapAllowlistExtension Gates the Immediate Pool Caller (Router) Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is that immediate caller, so the extension checks whether the **router** is allowlisted rather than the **end user**. If the pool admin allowlists the router (the only way to let legitimate users use the standard periphery path), every unpermissioned user can bypass the curated-pool gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of the pool — as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← immediate caller, not the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then enforces the allowlist against that forwarded `sender`:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`. The end user's address is never visible to the extension.

**The dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery path at all |
| Allowlist the router | Every user — allowlisted or not — can bypass the gate by routing through the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only specific addresses are supposed to be able to trade. The bypass lets any unpermissioned address execute swaps on that pool by routing through `MetricOmmSimpleRouter`. This directly violates the pool's access-control invariant and allows unauthorized users to extract output tokens from the pool's liquidity, causing direct loss to LPs who deposited under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers that the router is allowlisted on a curated pool can exploit this immediately with a single `exactInputSingle` call — no special privileges, no setup, no flash loan required. The router address is fixed and publicly known, so the bypass is trivially reachable by any on-chain actor.

---

### Recommendation

The extension must gate the **end user**, not the immediate pool caller. Two viable approaches:

1. **Pass the original `msg.sender` through `extensionData`**: The router encodes the end user's address into `extensionData`, and the extension decodes and checks it. This requires a trusted router convention and extension-side decoding.

2. **Check `sender` only when `sender` is not a known periphery contract; otherwise decode the real user from `extensionData`**: More complex but backward-compatible.

3. **Simplest correct fix**: Remove router-level allowlisting entirely and require users to call `pool.swap()` directly when the pool is allowlist-gated. Document this constraint explicitly so pool admins do not allowlist the router.

---

### Proof of Concept

**Setup:**
- Pool deployed with `SwapAllowlistExtension` as `EXTENSION_2`, `beforeSwap` order = `2`.
- Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
- Pool admin also calls `swapExtension.setAllowedToSwap(pool, router, true)` — necessary so Alice can use the standard router.

**Attack (Bob, a non-allowlisted user):**

```solidity
// Bob calls the router directly — no allowlist entry for Bob
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token0,
        recipient:       bob,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[pool][router] == true  ✓
// Bob's swap executes — allowlist bypassed
```

**Trace:**
1. `router.exactInputSingle` → `pool.swap(recipient=bob, ...)` with `msg.sender = router`
2. `pool._beforeSwap(sender=router, ...)`
3. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`
4. Swap executes; Bob receives output tokens from the curated pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
