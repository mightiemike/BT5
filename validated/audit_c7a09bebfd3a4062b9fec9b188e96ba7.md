### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` is called with `msg.sender` = router, so `sender` = router address. If the pool admin allowlists the router (a necessary step for any router-mediated swap to work), every user on the network can bypass the allowlist restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the router, so `sender` forwarded to the extension is the router address — not the end user. The allowlist check becomes `allowedSwapper[pool][router]`. If the router is allowlisted (which the admin must do to permit any router-mediated swap), the check passes for every caller regardless of their own allowlist status.

The same path applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of counterparties (e.g., KYC'd traders, institutional partners). The bypass allows any unprivileged address to trade against the pool's LP reserves at live oracle prices. LPs bear the full adverse-selection risk from counterparties the pool was explicitly configured to exclude. This is a direct loss of LP principal through unfavorable swap execution by unauthorized parties.

---

### Likelihood Explanation

The trigger requires two conditions, both of which are routine in production:

1. A pool is deployed with `SwapAllowlistExtension` in its extension order.
2. The pool admin allowlists the router so that normal users can swap through it.

Once both are true, every address on the network can bypass the allowlist with a single public router call. No special privilege, flash loan, or multi-step setup is needed.

---

### Recommendation

The extension must check the actual end-user identity, not the intermediary router. Two sound approaches:

1. **Extension-data forwarding**: Require the router to encode the originating user in `extensionData` and have the extension decode and verify it. The pool admin allowlists users, not the router.
2. **Direct-only policy**: Document and enforce (via `onlyPool` + a registry of approved callers) that the allowlist only applies to direct pool calls, and provide a separate router-aware allowlist extension that decodes the real sender from `extensionData`.

The current `BaseMetricExtension.beforeSwap` base already accepts `extensionData`; the router already forwards `params.extensionData` to the pool unchanged, so the forwarding plumbing exists. [6](#0-5) 

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension index 1).
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // necessary for router-mediated swaps

Attack
──────
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          pool,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           extensionData: ""
       })

5. Router calls pool.swap(bob, true, X, ..., "")
   → inside pool: msg.sender = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true  ✓  (check passes)

6. Bob's swap executes at oracle prices against LP reserves.
   Alice's allowlist entry is irrelevant; bob was never checked.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
