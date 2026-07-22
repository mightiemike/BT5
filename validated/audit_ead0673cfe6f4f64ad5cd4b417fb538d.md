### Title
`SwapAllowlistExtension` Bypass via Router: Wrong Actor Checked Allows Any User to Trade on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If a pool admin allowlists the router address (the only way to enable router-mediated swaps for their curated users), every unpermissioned user can bypass the allowlist by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC-verified addresses).
2. To let those users trade via the standard periphery, the admin must also call `setAllowedToSwap(pool, router, true)` — otherwise every router-mediated swap reverts.
3. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for all callers. Any unpermissioned user calls `router.exactInputSingle(pool, ...)` and the extension check passes unconditionally.

The `DepositAllowlistExtension` does **not** share this flaw because it checks the `owner` argument (the position owner explicitly passed to `addLiquidity`), not `sender`: [5](#0-4) 

---

### Impact Explanation

A curated pool whose swap allowlist is intended to restrict trading to a defined set of addresses (institutional, KYC-gated, or compliance-restricted pools) is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps, drain one-sided liquidity at oracle price, and extract value that the pool admin intended to reserve for permissioned counterparties. This is a direct loss of LP assets and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any pool admin who wants their allowlisted users to be able to use the router — a normal operational expectation — must allowlist the router address. The bypass is therefore triggered by a routine, expected admin action, not a misconfiguration. Any unpermissioned user who discovers the pool can exploit it immediately with a single router call.

---

### Recommendation

Pass the originating end-user address through the swap path so extensions can gate on it. Two options:

1. **Preferred:** Add a `swapper` field to the pool's `swap()` signature (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension reads this field instead of the pool-level `sender`.
2. **Minimal:** Document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the factory level (e.g., reject pools that configure both a swap allowlist extension and a mutable price provider accessible via the router). This does not fix the root cause but prevents the misconfiguration.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)      // required for alice to use the router
  LP adds liquidity

Attack (bob is NOT allowlisted):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives tokens from the curated pool
```

`allowedSwapper[pool][bob]` is `false`, but the check never reaches bob's address. The allowlist is silently bypassed.

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
