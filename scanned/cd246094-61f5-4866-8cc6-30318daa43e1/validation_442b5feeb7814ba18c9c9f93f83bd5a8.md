### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router address rather than the actual swapper. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then enforces the allowlist against that `sender` parameter: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

The pool therefore receives `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The allowlist is blind to the real end user.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to the recursive `_exactOutputIterateCallback` path where intermediate hops call `pool.swap` with `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

Two fund-impacting failure modes arise:

**Mode 1 — Full allowlist bypass (high severity).** A pool admin who wants to permit router-mediated swaps for their approved users must add the router to `allowedSwapper`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every caller. Any unpermitted user can call `exactInputSingle` (or any other router entry point) against the curated pool and the extension passes unconditionally. The allowlist provides zero protection. Disallowed users can drain LP-owned liquidity at oracle-quoted prices, causing direct loss of LP principal.

**Mode 2 — Broken router access for permitted users (medium severity).** If the pool admin does not allowlist the router, `allowedSwapper[pool][router]` is `false` and every router-mediated swap reverts with `NotAllowedToSwap`, even for users who are individually allowlisted. Permitted users are locked out of the supported periphery path, breaking core swap functionality.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any address can call `exactInputSingle` with an arbitrary pool address. No special role, token, or setup is required beyond holding the input token. The bypass is reachable in a single transaction by any unpermitted user once the router is allowlisted.

---

### Recommendation

The extension must gate the actual end user, not the intermediary router. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` for each hop, and the extension decodes and checks that address instead of the `sender` parameter.
2. **Dedicated sender field**: Add a `realSender` field to the swap parameters that the pool populates from a trusted source (e.g., a transient slot set by the router before calling `pool.swap`), and have the extension read that field.

The `DepositAllowlistExtension` avoids this problem because it gates `owner` (the position owner explicitly passed to `addLiquidity`), not the `sender` (the adder contract). The swap extension should adopt the same pattern of checking the economically relevant actor. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1).
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is permitted
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob is NOT permitted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         tokenIn: token0,
         amountIn: X,
         recipient: bob,
         ...
     })
  5. Router calls pool.swap(bob_recipient, ...) — pool.msg.sender = router
  6. _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] → true → passes
  7. Swap executes; bob receives output tokens from LP-owned liquidity.

Result: bob, a disallowed user, successfully swaps on a curated pool.
         The allowlist is completely bypassed via the public router.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
