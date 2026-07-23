### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address, Not the End User — Any User Can Bypass a Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (a natural step to enable router-based swaps for permitted users), every user — including those not individually allowlisted — can bypass the per-user gate by calling any of the router's `exact*` entry points.

### Finding Description

**Allowlist check in `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by the pool, which is the pool's own `msg.sender`.

**Pool passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

**Router calls the pool directly — the user's address is never forwarded:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,   // output recipient only
        params.zeroForOne,
        ...
    );
```

The router stores the original `msg.sender` only in transient storage for the payment callback (`_setNextCallbackContext(..., msg.sender, ...)`), but never passes it to the pool's `swap` call. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**Consequence:** The allowlist cannot distinguish between individual users when they route through the router. Allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) and then allowlists the router to let those users trade conveniently inadvertently opens the pool to every address on-chain. Any unpermissioned user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and execute swaps that the allowlist was meant to block. This breaks the core access-control invariant of the extension and allows unauthorized parties to drain pool liquidity at oracle prices, directly impacting LP principal.

### Likelihood Explanation

The scenario is highly plausible in production:
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict access.
2. Admin allowlists a handful of trusted addresses.
3. Admin also allowlists the router so those trusted addresses can use the standard periphery (a natural operational step — the alternative is forcing all permitted users to call the pool directly).
4. Any unpermissioned user discovers the router is allowlisted and routes through it.

No privileged action by the attacker is required; only a standard `exactInputSingle` call to the public router.

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the router's swap parameters and forward it as the `sender` argument to `pool.swap`. The pool interface already accepts a `sender` argument distinct from `msg.sender` in the extension callback, so no core changes are needed beyond the router forwarding the correct address.

2. **Validate in the extension that `msg.sender` (the pool's caller) is a trusted intermediary before trusting the forwarded `sender`.** If the pool is called by the router, the extension should check the forwarded user identity; if called directly, check `msg.sender`. This requires the router to pass the user address in `extensionData` and the extension to decode it, verifying the caller is the known router.

As a minimum short-term mitigation, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(true)` and that per-user gating is only enforceable for direct pool callers.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)      // alice is permitted
  admin calls swapExtension.setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool:      pool,
        recipient: bob,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result: bob bypasses the per-user allowlist and swaps in a restricted pool.
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
