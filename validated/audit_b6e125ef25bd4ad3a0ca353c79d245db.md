Audit Report

## Title
SwapAllowlistExtension Bypassed via Router Because `sender` Is Router Address, Not End User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address. If the pool admin allowlists the router — a necessary step for allowlisted users to use the router — every caller of the router, including non-allowlisted users, passes the gate. The allowlist is completely defeated.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — Extension checks `allowedSwapper[pool][sender]`:**

`beforeSwap` in `SwapAllowlistExtension` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

**Step 3 — Router calls `pool.swap()` as itself:**

`exactInputSingle` calls `pool.swap()` directly. The pool's `msg.sender` is the router address. The true end user (`msg.sender` of the router call) is stored only in transient callback context via `_setNextCallbackContext` and is never forwarded to the pool or extension: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` directly as the router. [4](#0-3) 

**Step 4 — The bypass:**

A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for **any** caller of the router regardless of their own allowlist status. The extension has no mechanism to recover the true end user — the payer address in transient storage is only used in the swap callback for payment, never surfaced to `beforeSwap`. [5](#0-4) 

## Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for restricting swap access to specific counterparties (KYC-gated pools, institutional-only pools, pools with favorable pricing for specific parties). When the router is allowlisted — a necessary and expected configuration for allowlisted users to use the standard periphery entry point — the allowlist is completely bypassed by any public user. Non-allowlisted users gain full swap access to pools designed to exclude them. This matches the audit pivot: "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router, multicall, callbacks, owner/salt separation, or alternate pool action."

## Likelihood Explanation

The router is the standard periphery entry point. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. This is a completely natural and expected administrative action. Once done, the bypass is available to any public user with no special privileges, no malicious setup, no non-standard token behavior, and no special knowledge beyond knowing the router address. The attack is repeatable and requires only a standard router call.

## Recommendation

The extension must verify the economic actor, not the immediate caller. Two concrete options:

1. **Pass the true payer through `extensionData`**: The router encodes `msg.sender` (the true user) in `extensionData`; the extension reads and verifies it against the allowlist. Requires a convention between router and extension.
2. **Trusted forwarder pattern**: The extension maps trusted intermediaries (e.g., the router) to a "read payer from `extensionData`" mode, verifies the encoded payer address against the allowlist, and falls back to checking `sender` directly for non-trusted callers.

Option 2 is preferred as it preserves backward compatibility for direct pool calls while correctly handling router-mediated flows.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true       // alice is KYC'd
  allowedSwapper[pool][router] = true      // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker] = false   // attacker is NOT allowlisted

Attack:
  attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  → router calls pool.swap(attacker, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for attacker

Assert:
  attacker successfully swaps on a pool they should be excluded from.
  The allowlist is completely bypassed.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, router, true)
  4. vm.prank(attacker); router.exactInputSingle(...)
  5. Assert swap succeeds (no NotAllowedToSwap revert)
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
