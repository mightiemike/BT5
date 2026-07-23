### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. The allowlist therefore gates the router address rather than the actual swapper. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

At that point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the **router address**, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

Two exploitable outcomes follow:

1. **Bypass path**: The pool admin must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, every user — including those the allowlist was meant to exclude — can swap by routing through `MetricOmmSimpleRouter`.

2. **Lockout path**: If the admin does not allowlist the router, every allowlisted user is silently blocked from using the router, forcing them to call the pool directly and losing slippage protection, multi-hop routing, and deadline enforcement.

The `DepositAllowlistExtension` does not share this flaw because it gates the explicit `owner` parameter, which callers supply directly and which the `MetricOmmPoolLiquidityAdder` preserves faithfully.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The allowlist provides no protection on the router path. LP funds in the pool are exposed to swaps from actors the pool admin explicitly intended to exclude, which can include MEV bots, sanctioned addresses, or competitors. This is a direct broken-core-functionality / admin-boundary-break impact.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user aware of the allowlist restriction can trivially bypass it by calling the router instead of the pool directly. No special privileges, flash loans, or multi-step setup are required. The bypass is reachable on every swap through the router.

---

### Recommendation

The allowlist must gate the economically relevant actor, not the immediate caller of `pool.swap()`. Two approaches:

**Option A — Pass the originating user explicitly.** Add a `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before forwarding to the pool. The extension then checks that field.

**Option B — Check `recipient` as a proxy.** If the pool's design cannot change, the extension can check `recipient` (the address that receives output tokens) instead of `sender`. This is imperfect but closer to the economic actor for single-hop swaps.

**Option C — Allowlist the router and enforce identity inside the router.** Gate the router itself with an on-router allowlist that verifies `msg.sender` before forwarding to the pool. This keeps the pool interface unchanged but requires the router to be trusted and non-upgradeable.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = false // router not explicitly allowed

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    // msg.sender inside pool = router

  pool calls _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == false  → revert

  → bob is blocked. But if admin allowlists router to let alice use it:

  allowedSwapper[pool][router] = true

  bob calls router.exactInputSingle again:
    extension checks allowedSwapper[pool][router] == true → passes
    bob's swap executes despite not being on the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
