### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist, keyed by `msg.sender` (the pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — making the pool's `msg.sender` the router, not the end user: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) [5](#0-4) 

In every router path, the pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

This creates two broken states:

1. **Allowlist bypass**: If the pool admin allowlists the router address (the only way to make router-mediated swaps work), every user on the network can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension passes — regardless of whether that user is individually allowlisted.

2. **Allowlisted users broken**: If the pool admin allowlists individual user addresses but not the router, those users cannot swap through the router even though they are explicitly permitted. The router is always blocked.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (explicitly supplied by the caller), not `msg.sender` of the pool: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted bots) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to unrestricted swaps from any address, directly contradicting the pool admin's access-control intent. LP funds can be drained by unauthorized traders at oracle-quoted prices.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that (a) has `SwapAllowlistExtension` configured and (b) needs to support router-mediated swaps must allowlist the router, which immediately opens the bypass to all users. This is the expected operational configuration, making the bypass reachable by any unprivileged user with no special preconditions.

---

### Recommendation

Pass the originating user identity through the swap path rather than relying on `msg.sender` at the pool boundary. Two approaches:

1. **Router forwards user identity**: Add a `sender` parameter to `IMetricOmmPoolActions.swap()` that the router populates with `msg.sender` before calling the pool. The pool passes this explicit `sender` to extensions instead of its own `msg.sender`.

2. **Extension reads from callback context**: Have the router store the user address in transient storage (it already uses transient storage for callback context) and expose a read function that extensions can call to retrieve the true originator.

Either approach ensures `SwapAllowlistExtension` checks the economically relevant actor — the user — rather than the intermediary router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(...) — pool's msg.sender = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - Extension checks allowedSwapper[pool][router] == true  ✓  (passes)
  - Swap executes for attacker despite attacker not being allowlisted

Result:
  - Allowlist is completely bypassed; attacker swaps freely in a restricted pool.
``` [7](#0-6) [8](#0-7) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
