### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural configuration for a pool that wants to accept router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user restriction by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to its own `msg.sender` — the direct caller of `MetricOmmPool.swap()`. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes and forwards `sender` verbatim: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no `sender` argument — the pool sees `msg.sender = router`: [3](#0-2) 

The same pattern holds for `exactInput` (intermediate hops use `address(this)` = router) and `exactOutput`: [4](#0-3) 

The result is a structural mismatch: the allowlist is keyed by `allowedSwapper[pool][sender]`, but `sender` is the router address for all router-mediated swaps, not the end user.

**Two broken invariants arise simultaneously:**

1. **Bypass path**: If the pool admin allowlists the router address (e.g., to permit router-mediated swaps from trusted users), every unprivileged user can bypass the per-user restriction by routing through `MetricOmmSimpleRouter`. The check `allowedSwapper[pool][router] == true` passes for all of them.

2. **Broken functionality path**: If the pool admin allowlists specific users but not the router, those allowlisted users cannot use the router at all — their swaps revert `NotAllowedToSwap` even though they are individually permitted. This makes the router unusable for any curated pool.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed through the call chain), so the deposit guard does not share this flaw: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` and `allowAllSwappers[pool] = false` relies on the allowlist to restrict who may trade against its liquidity. If the router is allowlisted (the natural configuration for a pool that wants to support the official periphery), any unprivileged user can execute swaps at oracle-derived prices by routing through `MetricOmmSimpleRouter`. This breaks the curation invariant and exposes LP assets to unauthorized counterparties. The pool admin cannot simultaneously allow router-mediated swaps and enforce per-user restrictions — the two goals are mutually exclusive under the current design.

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. A pool admin who wants to support the official router for allowlisted users will naturally add the router to the allowlist, unknowingly opening the bypass to all users. The misconfiguration requires no privileged access and is reachable by any unprivileged actor in a single transaction.

### Recommendation

The extension should check the **end user** identity, not the direct pool caller. One approach: pass the original `msg.sender` of the router call through `extensionData` and verify it in the hook. A cleaner approach is to have the router forward the originating user as a dedicated field, or to have the pool expose a `tx.origin`-equivalent context (though `tx.origin` itself is unsafe). The most robust fix is to redesign the allowlist to key on `owner`/`recipient` rather than `sender`, mirroring how `DepositAllowlistExtension` correctly keys on `owner`.

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension:
//   allowAllSwappers[pool] = false
//   allowedSwapper[pool][router] = true   ← admin allowlists the router
//   allowedSwapper[pool][alice]  = true   ← alice is individually allowed
//   allowedSwapper[pool][eve]    = false  ← eve is NOT allowed

// Eve bypasses the allowlist:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: eve,
    zeroForOne: true,
    amountIn: 1e18,
    ...
}));
// Extension checks allowedSwapper[pool][router] == true → passes
// Eve's swap executes despite not being individually allowlisted
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
