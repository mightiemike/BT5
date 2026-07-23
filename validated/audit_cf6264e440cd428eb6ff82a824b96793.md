### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for approved users), every user on the network can bypass the per-user allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
```

At this point `msg.sender` inside `pool.swap()` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

The same substitution occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin who wants approved users to be able to swap via the standard periphery must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for **every** caller who routes through `MetricOmmSimpleRouter`, regardless of whether that caller is on the per-user allowlist. The per-user curation is completely nullified: any address on the network can execute swaps against the curated pool by calling the router.

This is a direct loss-of-curation impact: the pool was deployed specifically to restrict trading to approved counterparties (e.g., KYC'd users, whitelisted institutions), and the bypass lets unapproved users trade against its liquidity, exposing LPs to unintended counterparty risk and potentially draining the pool at oracle-derived prices.

---

### Likelihood Explanation

The trigger is a single, natural admin action: allowlisting the router so that approved users can use the standard periphery. No privileged attacker knowledge is required beyond knowing the router address. Any user who discovers the router is allowlisted can immediately exploit the bypass. The router is a public, documented contract, so the bypass is trivially discoverable.

---

### Recommendation

The extension must gate on the **end user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass `tx.origin` as an additional parameter** in the hook interface so extensions can check the originating EOA (introduces its own risks with smart-contract wallets).

2. **Preferred — check `sender` against the allowlist only when `sender` is not a recognized router; otherwise check the payer stored in the router's transient context.** This requires the extension to be router-aware, which is architecturally undesirable.

3. **Cleanest — require direct pool interaction for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is not an EOA, or require the pool admin to allowlist individual users and never the router.

The simplest safe fix is to document and enforce that the router must **never** be added to `allowedSwapper` for a curated pool, and to add a guard in `SwapAllowlistExtension` that reverts if `sender` has contract code (i.e., is not an EOA), preventing router-mediated bypass entirely.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to let approved users use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not on allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: curated_pool,
           recipient: attacker,
           ...
       })
  2. Router calls pool.swap(attacker, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(router, attacker, ...).
  4. Extension evaluates:
       allowedSwapper[pool][router]  →  true   ✓
     Check passes. Swap executes.
  5. Attacker receives output tokens from the curated pool
     despite never being individually approved.

Result: Per-user allowlist is fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
