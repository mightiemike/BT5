### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the router is allowlisted (the only way to let router-mediated swaps through), every user — including those explicitly excluded — can bypass the curated pool's swap allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the **router address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual user's allowlist status is never consulted.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` parameter (the position owner), which the pool passes explicitly and which the liquidity adder also sets to the real depositor: [5](#0-4) 

---

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of addresses deploys a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook. To allow those users to also use the router (the primary supported periphery path), the admin must allowlist the router address. Once the router is allowlisted, **any** address — including explicitly excluded addresses — can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router and the extension will pass them through, because it only sees the router's address. The curated pool's access control is completely defeated for all router-mediated swaps. This constitutes a direct policy bypass on a curated pool and, depending on the pool's purpose (e.g., institutional-only, KYC-gated, whitelist-only liquidity), can result in unauthorized trading and direct loss of the pool's curation guarantee.

---

### Likelihood Explanation

The router is the primary supported periphery swap path. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The only precondition is that the pool admin has allowlisted the router (which is the only way to allow legitimate router-mediated swaps), making the bypass trivially reachable in any real deployment.

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to check the `recipient` parameter or, better, require the pool to pass the **original user** as a dedicated field. The cleanest fix is to mirror the deposit allowlist pattern: have the router forward the real user's address in `extensionData`, and have the extension decode and check that address. Alternatively, the pool's `swap` interface could be extended with an explicit `swapper` field (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender` before calling the pool, and the pool passes that field — not its own `msg.sender` — to the extension.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` on the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required to allow any router-mediated swap.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` (or simply never allowlists Alice).
4. Alice calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. Alice's swap executes despite being excluded from the allowlist.

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
