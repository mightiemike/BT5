### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the originating user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their permitted users), every unpermitted user can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The result is a structural identity mismatch:

| Call path | `sender` seen by extension | Allowlist entry required |
|---|---|---|
| `user → pool.swap()` | `user` | `allowedSwapper[pool][user]` |
| `user → router → pool.swap()` | `router` | `allowedSwapper[pool][router]` |

A pool admin who wants to permit allowlisted users to also use the router must add `allowedSwapper[pool][router] = true`. Once that entry exists, **every** address — including addresses that were never allowlisted — can call `router.exactInputSingle / exactInput / exactOutputSingle / exactOutput` and the extension will pass, because the check resolves to `allowedSwapper[pool][router]`, which is `true`.

The same identity collapse occurs in the multi-hop `exactOutput` callback path, where the router calls `pool.swap()` from inside `_exactOutputIterateCallback`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a restricted venue: only approved counterparties may trade against its oracle-priced liquidity. Bypassing the guard lets any unpermitted address execute swaps at the oracle bid/ask, extracting value from LP positions at prices the LPs agreed to offer only to vetted counterparties. This is a direct loss of LP principal — the pool settles real token transfers at oracle prices for trades it was configured to reject.

---

### Likelihood Explanation

The trigger is a single, natural admin action: allowlisting the router so that permitted users can access the pool through the standard periphery. Any pool that (a) has `SwapAllowlistExtension` active and (b) has added the router to its allowlist is immediately exploitable by any address. The router is a public, permissionless contract; no special capability is required beyond calling it.

---

### Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router.** Add an `originSender` field to the router's swap parameters and have the pool forward it as a separate argument to extensions, or encode it in `extensionData` with a signature the extension can verify.

2. **Check `tx.origin` only as a last resort** (not recommended for general use, but acceptable in a tightly scoped allowlist context where flash-loan and contract-wallet risks are already accepted by the pool admin).

Until fixed, pool admins must choose between (a) not allowlisting the router — blocking all router-mediated swaps for everyone — or (b) allowlisting the router and accepting that the allowlist is effectively disabled.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Admin calls setAllowedToSwap(pool, router, true)      // router added so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes at oracle price, draining LP value
  - Direct call: bob calls pool.swap() directly → sender=bob → allowedSwapper[pool][bob]==false → reverts
```

The bypass is reachable through `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` — all public entry points of `MetricOmmSimpleRouter`. [6](#0-5) [7](#0-6) [8](#0-7)

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
