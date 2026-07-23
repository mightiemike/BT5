All four cited files have been read and verified against the actual production code. Every step of the exploit chain is confirmed:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` — i.e., the router address when called via router [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` and `exactInput` call `pool.swap(...)` directly, making the pool see `msg.sender = router` [4](#0-3) 

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently grants every address — including explicitly excluded ones — the ability to bypass the allowlist by calling through the router.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at lines 230–240. `ExtensionCalling._beforeSwap` encodes this value unchanged as the `sender` argument in `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` at lines 160–177. There is no mechanism to carry the original end-user address through this call chain.

**Step 2 — `SwapAllowlistExtension` gates on that `sender`.**

`beforeSwap` checks `!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]` at line 37. When a user calls the pool directly, `sender` is the user — correct. When a user calls through the router, `sender` is `address(router)`.

**Step 3 — The router calls the pool as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` (lines 72–80) and `exactInput` (lines 104–112) both call `IMetricOmmPoolActions(pool).swap(...)` directly. The pool receives `msg.sender = address(router)` and passes it as `sender` to the extension.

**Step 4 — The bypass.**

A pool admin who wants to support router-mediated swaps for allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call arriving through the router — regardless of who the actual end user is. Any address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and the allowlist check passes silently.

**Why existing guards fail:**

The only guard is `allowedSwapper[pool][sender]`. There is no `originalSender` field in the extension interface, no mechanism for the router to forward the true initiator, and no way for the extension to distinguish a permitted user routing through the router from an unpermitted one. The `DepositAllowlistExtension` avoids this because it gates on `owner` (explicitly supplied by the caller), not on `sender` (the intermediary).

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade in a pool. Bypassing it allows any unpermissioned address to execute swaps against a pool intended to be restricted (e.g., KYC-gated, institutional-only, or regulatory-compliant pools). The attacker receives real token output from the pool in exchange for token input — a direct, fund-impacting consequence. LP providers are exposed to trades from counterparties the pool admin explicitly intended to exclude. This constitutes a broken core pool access-control invariant with direct financial impact, meeting the "Admin-boundary break bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router address. This is the natural and expected configuration for any allowlist-protected pool that also wants to support the standard periphery router. The admin has no alternative: there is no way to simultaneously (a) allow router-mediated swaps for permitted users and (b) block router-mediated swaps for unpermitted users, because the extension receives only the router's address. The misconfiguration is therefore not a mistake — it is the only available configuration for router-compatible allowlisted pools. The attack is repeatable by any address at any time once the router is allowlisted.

## Recommendation
The `SwapAllowlistExtension` must gate on the end user, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` (the actual user) as an additional field in `extensionData` or as a dedicated `originalSender` argument, and the extension should read and verify it from there.

2. **Introduce a dedicated `originalSender` field in the extension interface** so the pool can carry the true initiator through the call chain, similar to how `DepositAllowlistExtension` uses the explicitly supplied `owner` parameter rather than `sender`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Admin calls setAllowedToSwap(pool, alice, true)    // alice is the only permitted user
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — pool sees msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. bob receives token output.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; bob bypasses the allowlist.

Foundry test outline:
  - Deploy SwapAllowlistExtension, configure pool with it as beforeSwap hook.
  - vm.prank(admin); extension.setAllowedToSwap(pool, address(router), true);
  - vm.prank(admin); extension.setAllowedToSwap(pool, alice, true);
  - vm.prank(bob); router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
  - Assert: swap succeeds (no revert), demonstrating bypass.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
