### Title
SwapAllowlistExtension Checks Router Address as Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every unpermissioned user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the `sender` argument to `_beforeAddLiquidity`: [1](#0-0) 

The `swap` function follows the same pattern, passing `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the pool's immediate caller) is allowlisted: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract, not the end user: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The router stores the original `msg.sender` only in transient storage for payment callbacks — it is never forwarded to the pool as the swap `sender`: [5](#0-4) 

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade through the router |
| **Allowlist the router** | Every non-allowlisted user can bypass the guard via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) is fully bypassed. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP assets at oracle-derived prices. LP principal is exposed to unrestricted counterparties the pool admin explicitly intended to exclude. This is a direct loss-of-policy impact on LP funds and constitutes a broken core pool functionality (the allowlist guard).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, production-deployed periphery swap entry point. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. This is the natural and expected production configuration, making the bypass reachable by any unpermissioned user with no special privileges or setup.

---

### Recommendation

The `sender` forwarded to extension hooks must represent the economically relevant actor — the end user — not the intermediate router. Two viable approaches:

1. **Pass the original caller through the router**: Have the router forward `msg.sender` explicitly as a parameter to `pool.swap`, and have the pool pass that value (after validating it comes from a trusted router) as `sender` to extensions.
2. **Check `tx.origin` in the extension** (weaker, not recommended for general use): Only acceptable in contexts where `tx.origin` is a reliable proxy for the user.

The cleanest fix is approach 1: add a `sender` override parameter to `IMetricOmmPoolActions.swap` that trusted periphery contracts can populate, with the pool verifying the caller is a factory-registered router before accepting the override.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router must be allowlisted for alice to use it
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender = router
  5. allowedSwapper[pool][router] == true  →  check passes
  6. bob's swap executes against LP assets at oracle price

Result: bob, a non-allowlisted user, successfully trades on a curated pool,
        bypassing the allowlist guard entirely.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
