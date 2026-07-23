### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Curated Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` passed to the extension is the router's address — not the actual user. Any unprivileged user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` delivered to the extension is the router — not the actual user. The extension has no visibility into who called the router.

This creates an irresolvable identity mismatch analogous to the external report's stale-account bug: the guard is bound to the wrong actor. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot swap through the router at all.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through the router, because the extension sees only the router's address and approves it unconditionally.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` argument (the position owner explicitly supplied by the caller), which the `MetricOmmPoolLiquidityAdder` correctly threads through from the user's input: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted addresses (e.g., for regulatory compliance, liquidity-pool access control, or protocol-fee gating) is fully bypassable by any user who routes through `MetricOmmSimpleRouter`. The attacker receives the same swap execution — including oracle-priced output tokens — as an allowlisted user, with no loss of funds to themselves and direct violation of the pool's access-control invariant. Pools that depend on the allowlist to gate flows tied to whitelisted LP addresses or fee-tier access suffer a broken core pool functionality with direct fund-impact consequences (unauthorized parties extract value from curated pools).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint documented and expected to be used by all integrators and end users. Any actor who reads the periphery contracts or observes on-chain transactions can discover the bypass. No privileged access, special token behavior, or malicious setup is required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **real initiating user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router.** Add a `swapper` field to the swap call or extension payload that the router populates with `msg.sender` before calling the pool. The extension reads this field instead of the `sender` argument.

2. **Check `sender` only for direct pool calls; require the router to forward the real user identity via `extensionData`.** The extension decodes the actual swapper from `extensionData` when `sender` is a known router, and verifies the decoded address against the allowlist.

Either approach must ensure the checked identity is the economically relevant actor — the address that initiated and will benefit from the swap — not the routing intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Router calls pool.swap(bob, true, X, ...) with msg.sender = router
  Pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true  ✓
  Swap executes; bob receives output tokens

Result:
  bob bypassed the allowlist entirely.
  If admin does NOT allowlist the router, alice also cannot use the router —
  the allowlist is either universally open to router users or universally
  closed to them, with no per-user discrimination possible.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
