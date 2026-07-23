### Title
`SwapAllowlistExtension` gates on the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router address (the only way to let any user swap through the router), every user — including those not individually allowlisted — can bypass the per-pool swap restriction.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← sender = whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So when a user calls `exactInputSingle`, the call chain is:

```
user → router.exactInputSingle() → pool.swap()  [msg.sender = router]
                                        ↓
                              beforeSwap(sender = router, ...)
                                        ↓
                         allowedSwapper[pool][router]  ← checked, not user
```

The pool admin faces an impossible choice:
- **Do not allowlist the router** → no user can ever swap through the router on this pool.
- **Allowlist the router** → every user, including those not individually allowlisted, can bypass the restriction by routing through the router.

There is no configuration that simultaneously allows allowlisted users to use the router while blocking non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-controlled bots) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can:

- Execute swaps against a pool intended to be closed to them.
- Drain liquidity at oracle-derived prices that the LP only intended to offer to trusted counterparties.
- Cause adverse selection losses to LPs whose pool design assumed a restricted trading set.

This is a direct loss of LP principal through unauthorized swap execution — matching the "broken core pool functionality causing loss of funds" criterion.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- The bypass requires only calling `exactInputSingle` or `exactInput` — standard user-facing operations.
- Any user who discovers the allowlist restriction can trivially route around it.
- Pool admins are unlikely to anticipate this because the `DepositAllowlistExtension` correctly gates on `owner` (the position owner, not the payer), creating a false expectation that the swap allowlist similarly gates on the end user.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the **end user** rather than the immediate caller. Two options:

1. **Preferred — check `sender` but require routers to forward the original user**: Define a standard interface that trusted routers implement to expose the originating user, and check that address instead of `sender`.

2. **Simpler — document and enforce direct-call-only**: Add an `onlyPool` guard that also verifies `sender == tx.origin` or that `sender` is not a known router, and document that router-mediated swaps are incompatible with the allowlist.

The `DepositAllowlistExtension` correctly gates on `owner` (the economically relevant party for deposits). The swap allowlist should apply the same principle: gate on the economically relevant party for swaps, which is the end user, not the routing intermediary.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // Alice is allowlisted
  allowedSwapper[pool][router] = true         // router allowlisted so Alice can use it
  allowedSwapper[pool][bob] = false           // Bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → pool.swap() called with msg.sender = router
    → beforeSwap(sender = router, ...)
    → allowedSwapper[pool][router] == true    ✓ passes
    → Bob's swap executes against the restricted pool
```

Bob successfully swaps against a pool he was explicitly excluded from, receiving tokens at oracle-derived prices the LP only intended to offer to allowlisted counterparties. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
