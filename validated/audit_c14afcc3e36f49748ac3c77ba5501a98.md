### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the router contract, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension hook.**

In `MetricOmmPool.swap`, the `sender` forwarded to `_beforeSwap` is always `msg.sender` of the pool call: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` against the allowlist.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. [2](#0-1) 

**Step 3 — `MetricOmmSimpleRouter` is the immediate caller of `pool.swap()`.**

`exactInputSingle` calls `pool.swap(...)` directly; the router is `msg.sender` of the pool: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The mismatch:** The allowlist is keyed on `allowedSwapper[pool][sender]`. When the router is the caller, `sender` = router address. The extension never sees the end user's address. There are two broken outcomes:

| Scenario | Result |
|---|---|
| Pool admin allowlists the router (to enable router usage) | Every user — including those not individually allowlisted — can swap by routing through the router |
| Pool admin does not allowlist the router | No user can swap through the router, even if individually allowlisted |

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the position owner, explicitly passed and representing the actual beneficiary), not `sender` (the LiquidityAdder contract): [5](#0-4) 

The swap extension has no equivalent mechanism to recover the real end user's identity.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production access-control gate for swap operations on restricted pools. Its complete bypass means:

- Unauthorized users can execute swaps on pools that are supposed to be permissioned (e.g., institutional or KYC-gated pools).
- Any LP principal deposited into a restricted pool is exposed to trades from actors the pool admin explicitly did not allowlist.
- The broken invariant is: *"only allowlisted addresses may swap"* — this invariant fails for all router-mediated swaps when the router is allowlisted.

This is a direct loss-of-access-control impact on user principal and pool integrity, matching the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" gate.

---

### Likelihood Explanation

The trigger requires no privilege. Any user can call `MetricOmmSimpleRouter`. The precondition — the router being allowlisted — is the natural and expected configuration for any pool that wants to support the standard periphery swap path alongside an allowlist. A pool admin who configures both features will almost certainly allowlist the router, inadvertently opening the pool to all users. The attack requires no special tokens, no malicious setup, and no admin cooperation beyond the normal deployment pattern.

---

### Recommendation

The extension must check the actual end user, not the immediate pool caller. Two viable mitigations:

1. **Pass the real user via `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address. This requires a convention between the router and the extension.

2. **Add a `realSender` field to the hook signature**: The pool could forward both `msg.sender` (the immediate caller) and an optional `realSender` (decoded from `extensionData` or a separate argument), letting extensions choose which identity to gate.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to enable router-mediated swaps.
3. Unauthorized user (address U, not in allowedSwapper[pool]) calls:
     router.exactInputSingle({pool: pool, recipient: U, ...})
4. Router calls pool.swap(U, ...) — msg.sender of pool = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
7. U successfully swaps on the restricted pool.
   allowedSwapper[pool][U] was never checked.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` is the router, not the end user. [6](#0-5)

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
