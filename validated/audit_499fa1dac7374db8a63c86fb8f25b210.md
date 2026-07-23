### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, making the allowlist either silently bypassable or broken for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates an irresolvable dilemma for any pool admin who wants to enforce a per-user allowlist while also supporting the router: either the router is not allowlisted (breaking router access for every user, including allowlisted ones) or the router is allowlisted (letting every user bypass the allowlist by routing through it).

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
              msg.sender = router
         → ExtensionCalling._beforeSwap(sender = router, ...)
         → SwapAllowlistExtension.beforeSwap(sender = router, ...)
              checks allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When the router is the direct caller, `sender = router`. The extension has no visibility into the original EOA.

The router calls the pool with itself as `msg.sender` on every hop: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` (e.g., to restrict trading to KYC'd counterparties or specific market makers) faces an irresolvable choice:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Every user — including allowlisted ones — is blocked from using `MetricOmmSimpleRouter`. Core router functionality is broken for the pool. |
| **Allowlists the router** | Any user, regardless of allowlist status, can bypass the per-user gate by routing through the public router. The allowlist is silently defeated. |

In the second case, a non-allowlisted user calls `exactInputSingle` or `exactInput` through the router; the extension sees `sender = router` (allowlisted), passes the check, and the unauthorized swap executes against the pool. LPs on a pool designed for restricted counterparties are exposed to adverse selection or front-running from actors the admin explicitly intended to exclude.

---

### Likelihood Explanation

A production deployment of a curated pool almost always needs router support (multi-hop paths, slippage protection, deadline enforcement). The admin has a strong natural incentive to allowlist the router. The bypass is therefore reachable through a normal, expected operational step, not an exotic configuration. Any user who discovers the router is allowlisted can exploit it permissionlessly.

---

### Recommendation

The extension must check the original EOA, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the call chain.** The pool could forward `tx.origin` or a caller-supplied `originator` field alongside `sender`. The extension would then check `allowedSwapper[pool][originator]`.

2. **Alternatively, check `sender` only when `sender` is an EOA, and reject contract callers that are not themselves on the allowlist.** This is stricter but prevents any unvetted intermediary from acting as a pass-through.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position beneficiary) rather than `sender` (the payer/caller): [6](#0-5) 

The swap allowlist should adopt the same pattern — gate on the economically relevant actor, not the intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for allowlisted users.
3. Non-allowlisted `userC` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userC` successfully trades on a pool they were explicitly excluded from, with no revert.

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
