## Analysis

**Tracing the full call chain:**

**Direct swap:** `User → pool.swap()` → `_beforeSwap(msg.sender=User, ...)` → `extension.beforeSwap(sender=User, ...)` → checks `allowedSwapper[pool][User]`

**Router swap:** `User → router.exactInputSingle()` → `pool.swap()` (msg.sender=Router) → `_beforeSwap(msg.sender=Router, ...)` → `extension.beforeSwap(sender=Router, ...)` → checks `allowedSwapper[pool][Router]`

The pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router is the caller, `sender` is the router address, not the end user. The router does not forward the original `msg.sender` to the pool in any way — it simply calls `pool.swap()` directly: [4](#0-3) 

**The dilemma this creates for pool admins:**

| Admin action | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Allowlist individual users only | ✅ passes | ❌ blocked | ❌ blocked |
| Also allowlist the router | ✅ passes | ✅ passes | ✅ **bypasses restriction** |

There is no configuration that allows "specific users via the router" without simultaneously allowing **all** users via the router. Allowlisting the router is the natural action a pool admin takes to let their allowlisted users use the standard router — but it silently opens the pool to everyone.

**On the other claimed attack vectors:**

- **Hook skip/reorder:** No evidence of this. `_callExtensionsInOrder` iterates the packed order bitmap deterministically with no reordering path.
- **Guard threshold after state-moving precursor:** `SwapAllowlistExtension` has no threshold-based guard; it is a pure allowlist. No state manipulation changes its outcome.
- **Remove-liquidity while paused:** `removeLiquidity` lacks `whenNotPaused` but this is intentional (users must be able to exit). It has no bearing on the swap allowlist.

The only real, concrete issue is the sender identity substitution.

**Impact assessment:**

The bypass requires the pool admin to allowlist the router — a natural and expected action. Once done, any unprivileged user can call `router.exactInputSingle()` targeting the restricted pool and the `beforeSwap` hook passes because it sees `sender = router`. The per-user restriction is completely nullified. This is a broken protection boundary, not merely a design inconvenience: the extension's stated purpose ("Gates `swap` by swapper address, per pool") is unachievable for router-mediated flows.

However, the impact is conditional on the pool admin having allowlisted the router. If the admin never allowlists the router, the bypass does not exist (though allowlisted users also cannot use the router). This conditionality limits severity.

---

### Title
Router Sender Identity Substitution Silently Bypasses `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the direct caller of `pool.swap()` as the swapper identity. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees the router address as `sender`, not the end user. A pool admin who allowlists the router (to let their allowlisted users use the standard router) simultaneously opens the pool to all users, completely nullifying the per-user restriction.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` uses this value to look up `allowedSwapper[pool][sender]`. When the router is the direct caller of `pool.swap()`, `sender` is the router address. The extension has no mechanism to recover the original end user's address.

The pool admin faces an impossible choice:
- Do not allowlist the router → allowlisted users cannot use the router at all.
- Allowlist the router → all users bypass the per-user restriction.

There is no configuration that achieves "only my allowlisted users may swap, including via the router."

### Impact Explanation
Any user can swap in a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, provided the pool admin has allowlisted the router address. The protection hook does not fail closed — it silently passes for all router callers. Depending on the pool's purpose (e.g., institutional-only, KYC-gated), this allows unauthorized principals to trade against restricted liquidity.

### Likelihood Explanation
Allowlisting the router is the expected operational action for any pool admin who wants their allowlisted users to access the pool through the standard periphery. The bypass is therefore likely to be triggered in any real deployment that uses `SwapAllowlistExtension` with the router.

### Recommendation
Pass the original end user's identity through the call chain. One approach: include the original `msg.sender` (the end user) in `extensionData` from the router, and have the extension verify it against a router-authenticated field. A cleaner approach is to have the pool accept an explicit `swapper` parameter distinct from `msg.sender`, validated by the caller, or to have the router expose a view that the extension can call to resolve the true payer. At minimum, document clearly that allowlisting the router grants access to all users, so pool admins are not misled.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as `EXTENSION_1` in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let `userA` use the router.
4. Non-allowlisted `userB` calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router] == true` → passes.
8. `userB` completes the swap in a pool they were never meant to access.

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
