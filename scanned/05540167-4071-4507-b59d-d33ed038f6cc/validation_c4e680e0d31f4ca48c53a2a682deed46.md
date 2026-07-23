### Title
`SwapAllowlistExtension` checks router address instead of actual user when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, creating a two-sided failure: allowlisted users cannot use the router at all, and if the pool admin allowlists the router to fix that, every non-allowlisted user can bypass the curated-pool gate.

---

### Finding Description

**Root cause — wrong actor in `SwapAllowlistExtension.beforeSwap`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and gates the swap on that value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of that call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

`msg.sender` of `pool.swap()` is therefore the **router address**. The extension checks `allowedSwapper[pool][router]`, never touching the actual user's address. The router has no mechanism to forward the original caller's identity to the extension — `extensionData` is passed through opaquely and `SwapAllowlistExtension` does not decode it.

**Contrast with `DepositAllowlistExtension`**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed by the caller), not `sender` (the immediate caller of `pool.addLiquidity`):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Because `owner` is the same regardless of whether the user goes through `MetricOmmPoolLiquidityAdder` or calls the pool directly, the deposit allowlist is not affected. The swap allowlist has no equivalent stable identity to check — it relies solely on `sender`, which changes when the router intermediates.

---

### Impact Explanation

Two concrete failure modes, both fund-impacting:

**Mode A — Allowlist bypass (High)**
A pool admin configures `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC-verified addresses). Allowlisted users need the router for slippage protection and multi-hop paths, so the admin also allowlists the router address. Any non-allowlisted user can now call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool; the extension sees `sender = router`, which is allowlisted, and the swap proceeds. The curated-pool invariant is fully broken: every user on the network can trade on a pool intended for a restricted set.

**Mode B — Broken core functionality for allowlisted users (High)**
If the admin does NOT allowlist the router (the natural choice for a curated pool), every allowlisted user who calls any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) will have their swap reverted with `NotAllowedToSwap`. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` themselves — not a realistic expectation for end users. The router, the primary user-facing swap interface, is effectively disabled for the pool.

Both modes are reachable by any unprivileged user with no special setup beyond a standard swap call.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol.
- Any pool that deploys `SwapAllowlistExtension` is immediately affected; the bypass requires only a standard router call.
- The admin faces a forced choice between Mode A and Mode B with no correct option under the current implementation.
- No special token behavior, malicious setup, or privileged access is required.

---

### Recommendation

The extension must check the **original user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Router forwards original sender via `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. This requires a coordinated change to both the router and the extension.

2. **Check `sender` only when it is an EOA; decode from `extensionData` otherwise**: The extension can inspect whether `sender` is a contract and, if so, require a signed or encoded original-user field in `extensionData`. This is more complex but avoids hardcoding router addresses.

3. **Structural fix — pass original user through the pool**: The pool could expose a transient "original initiator" slot (similar to the existing `inSwap` transient marker) that the router sets before calling `pool.swap()`. The extension reads that slot instead of `sender`. This is the cleanest fix but requires a core pool change.

---

### Proof of Concept

Call path demonstrating Mode A (allowlist bypass):

```
Pool admin:
  swapExtension.setAllowedToSwap(pool, router, true)   // allowlists router so legitimate users can trade
  // does NOT allowlist attacker (0xAttacker)

Attacker (0xAttacker, not in allowlist):
  MetricOmmSimpleRouter.exactInputSingle({
      pool:       <curated pool>,
      recipient:  attacker,
      zeroForOne: true,
      amountIn:   X,
      ...
  })
  // Router calls pool.swap(...) → msg.sender = router
  // _beforeSwap(sender=router, ...)
  // SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  // Swap executes — attacker bypasses the curated allowlist
```

Call path demonstrating Mode B (allowlisted user blocked):

```
Pool admin:
  swapExtension.setAllowedToSwap(pool, alice, true)   // allowlists alice
  // does NOT allowlist router

Alice (allowlisted):
  MetricOmmSimpleRouter.exactInputSingle({pool: <curated pool>, ...})
  // Router calls pool.swap(...) → msg.sender = router
  // _beforeSwap(sender=router, ...)
  // SwapAllowlistExtension checks allowedSwapper[pool][router] == false  ✗
  // → revert NotAllowedToSwap()
  // Alice cannot use the router at all
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
