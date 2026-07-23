Looking at the swap allowlist extension and how the router passes identity to the pool's `beforeSwap` hook.

**Key chain to trace:**

1. `MetricOmmSimpleRouter.exactInputSingle` → calls `pool.swap(params.recipient, ...)` with `msg.sender = router`
2. `MetricOmmPool.swap` → calls `_beforeSwap(msg.sender=router, recipient, ...)`
3. `ExtensionCalling._beforeSwap` → encodes `sender=router` and dispatches to extension
4. `SwapAllowlistExtension.beforeSwap` → checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The extension receives the router's address as `sender`, not the actual end-user's address. The actual user's identity is stored only in the router's transient callback context and is never forwarded to the pool or extension.

---

### Title
SwapAllowlistExtension gates on router address instead of actual swapper, allowing any user to bypass per-user swap restrictions via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router contract address, not the originating user. If the pool admin allowlists the router to support router-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is `msg.sender` of `pool.swap()`: [4](#0-3) 

The actual end-user's address (`msg.sender` of `exactInputSingle`) is stored only in the router's transient callback context for payment settlement and is never forwarded to the pool or extension. The extension therefore sees `sender = router`, not the originating user.

### Impact Explanation

If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for allowlisted users), the allowlist becomes a no-op for all router-mediated swaps: any unprivileged user calls `exactInputSingle` through the public router, the extension sees `sender = router` (allowlisted), and the swap proceeds. The per-user restriction is completely defeated. Pools designed to restrict swap counterparties — e.g., permissioned liquidity pools, KYC-gated pools, or pools with specific LP agreements — will accept swaps from arbitrary users, constituting a direct admin-boundary break with fund-impacting consequences (unauthorized parties drain or interact with restricted liquidity).

If the admin does not allowlist the router, the opposite failure occurs: individually allowlisted users cannot use the router at all, breaking core swap functionality for the intended user set.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Any user can call it without restriction. The bypass requires only that the pool admin has allowlisted the router — a necessary step to make the extension compatible with router-mediated swaps. The trigger is fully unprivileged and requires no special setup beyond a standard router call.

### Recommendation

The pool must forward the originating user's address to the extension, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension decodes and checks it. This requires a convention between router and extension.

2. **Add a `swapperOverride` field to the swap call**: The pool accepts an optional "on-behalf-of" address (authenticated by the immediate caller) and passes it as `sender` to extensions. The router would populate this with `msg.sender`.

The simplest safe fix is for `SwapAllowlistExtension` to document that it only gates direct `pool.swap()` callers and is incompatible with router-mediated flows, and for the router to expose a mechanism to embed the real user identity in `extensionData` that the extension can verify.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, tokenIn: ..., tokenOut: ..., ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Swap executes for attacker despite attacker not being allowlisted

Result:
  - attacker successfully swaps on a pool intended to restrict access
  - SwapAllowlistExtension is completely bypassed for all router-mediated swaps
```

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
