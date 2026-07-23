### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any user to bypass the swap allowlist when `MetricOmmSimpleRouter` is allowlisted — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is a production extension that gates `swap` by "swapper address, per pool." Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the immediate caller of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router — a natural configuration to enable router-mediated swaps — every user on-chain can bypass the allowlist by routing through the public router.

---

### Finding Description

**Call chain establishing `sender` identity:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist: [3](#0-2) 

**Where the identity breaks:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(recipient, ...)` directly — so `msg.sender` seen by the pool is the **router address**, not the end user: [4](#0-3) 

The same holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops via `_exactOutputIterateCallback`): [5](#0-4) [6](#0-5) 

**The bypass condition:**

The allowlist check resolves to `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to be able to swap through the official router must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, the guard passes for **every** caller of the router regardless of their individual allowlist status, because the extension never sees the end user's address.

The existing test suite confirms the intended model is per-user allowlisting of direct callers (`TestCaller` contracts), with no test covering the router-mediated path: [7](#0-6) 

---

### Impact Explanation

Any user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter` when the router is allowlisted. Depending on why the allowlist is in place this enables:

- Unauthorized access to oracle-priced liquidity in a private or KYC-gated pool.
- Disruption of pools restricted to specific market makers, allowing arbitrary parties to trade against the pool's liquidity at oracle prices.
- Broken admin-boundary: the pool admin's per-user access control is rendered ineffective by a public periphery contract.

This matches the allowed impact category: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."*

---

### Likelihood Explanation

**High.** The router is a public, permissionless contract. Pool admins have a strong incentive to allowlist it — without doing so, no allowlisted user can use the router at all (they must call the pool directly). The bypass requires no special privileges, no flash loan, and no unusual token behavior. Any user who can call `MetricOmmSimpleRouter` can exploit it the moment the router is allowlisted.

---

### Recommendation

The `beforeSwap` hook must gate the actual end user, not the immediate caller of `pool.swap()`. Two concrete options:

1. **Check `recipient` instead of `sender` for router-mediated swaps.** The router always sets `recipient` to the user-controlled address. However, `recipient` is also caller-controlled and can be set to any address, so this alone is insufficient.

2. **Require the actual user identity to be passed in `extensionData` and verified by the extension.** The router would encode `msg.sender` into `extensionData`; the extension would decode and check it. This requires a coordinated change to the router and the extension.

3. **Allowlist the router only when `allowAllSwappers` is the intended policy.** Document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and should never be used when per-user gating is required.

The cleanest fix is option 2: the extension should decode the true initiator from `extensionData` when `sender` is a known router, or the router should be redesigned to forward the end user's identity through a dedicated field.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin allowlists Alice:
       swapExtension.setAllowedToSwap(pool, alice, true)
3. Pool admin also allowlists the router so Alice can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
6. Pool calls _beforeSwap(router, recipient, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes successfully despite not being on the allowlist.
```

The allowlist is completely bypassed for all users as soon as the router is allowlisted, collapsing per-user access control to a binary open/closed gate identical to `setAllowAllSwappers(pool, true)`.

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
