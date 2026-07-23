### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Per-User Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter it receives from the pool. The pool always passes `msg.sender` of the `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][endUser]`. If the router is allowlisted (which is required for any router-mediated swap to work for legitimate users), every unpermissioned user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and calls the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: even allowlisted users cannot swap through the router; they must call `pool.swap()` directly, breaking the supported periphery path.
- **If the router IS allowlisted** (the only way to let legitimate users use the router): every address on the network can swap through the router and the per-user allowlist is completely defeated.

The analog to the external report is exact: the guard checks the wrong identity (`sender` = router) instead of the economically relevant actor (the end user), just as the Crowdsale guard checked `investedAmountOf` instead of `tokenAmountOf`.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., for regulatory compliance, KYC gating, or institutional-only access) with `SwapAllowlistExtension` cannot simultaneously:
1. Allow legitimate allowlisted users to use the public router, and
2. Block non-allowlisted users from swapping.

Any non-allowlisted user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). This is a **direct policy bypass** on a curated pool, allowing unauthorized parties to trade against LP funds that were deposited under the assumption that only vetted counterparties would interact.

**Severity: High** — broken core allowlist invariant with direct fund-impact consequence (unauthorized swaps drain LP inventory at oracle prices).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special setup is required: any EOA or contract can call `exactInputSingle` with the target pool.
- The bypass is deterministic and requires no timing, flash loans, or privileged access.
- The only precondition is that the pool admin has allowlisted the router (which is the only way to support router-mediated swaps for legitimate users).

**Likelihood: High.**

---

### Recommendation

The `sender` parameter passed to extensions must represent the **end user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as an explicit field in `extensionData` so that the extension can decode and verify the true initiator. The extension would then check the decoded user address rather than the `sender` parameter.

2. **In `SwapAllowlistExtension`**: document clearly that `sender` is the immediate pool caller, and provide a router-aware variant that decodes the true initiator from `extensionData` when the immediate caller is a known router.

A simpler but less flexible alternative is to require that allowlisted users always call `pool.swap()` directly and document that the router is incompatible with `SwapAllowlistExtension`. However, this breaks the supported periphery path and is not a real fix.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in beforeSwap order
  admin calls extension.setAllowedToSwap(pool, alice, true)
    → allowedSwapper[pool][alice] = true
  admin calls extension.setAllowedToSwap(pool, address(router), true)
    → allowedSwapper[pool][router] = true  (required for alice to use the router)

Attack (Bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for Bob despite Bob never being allowlisted

Verification:
  extension.isAllowedToSwap(pool, bob) == false   // Bob is not allowlisted
  // Yet Bob's swap succeeded because the router is allowlisted
```

The root cause is at: [6](#0-5) 

where `sender` is the router address, not the end user, making the per-user allowlist ineffective for all router-mediated swaps.

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
