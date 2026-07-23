### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router (required for any router-mediated swap to succeed) unconditionally opens the gate to every user, defeating the per-user curation the extension is meant to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

A pool admin who wants router-mediated swaps to work at all must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual end user is. The per-user curation is silently bypassed.

The same path exists for `exactInput` (multi-hop), `exactOutputSingle`, `exactOutput`, and `simulateSwapAndRevert`, all of which call `pool.swap` with the router as `msg.sender`. [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses). Once the router is allowlisted — which is necessary for any normal user to swap through the standard periphery — the allowlist is unconditionally bypassed for every user who routes through the router. Any disallowed address can execute swaps against the pool, draining LP-owned assets at oracle-derived prices. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who deploy a curated pool and want it to be usable through the router must allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — any address can call `exactInputSingle` on the router pointing at the curated pool. The only precondition is that the router is allowlisted, which is the expected operational state for any pool that supports router-mediated trading.

---

### Recommendation

The extension must gate on the **end user's identity**, not the intermediary's. Two sound approaches:

1. **Pass the original `msg.sender` through the router as an explicit parameter.** The router encodes the real user address in `extensionData`; the extension decodes and checks it. This requires a trusted router assumption, which is already implicit in the current design.

2. **Check `recipient` instead of `sender` for swap allowlisting.** The `recipient` is the address that receives output tokens and is the economically relevant actor. The extension already receives `recipient` as its second parameter (currently unnamed/ignored). Gating on `recipient` instead of `sender` would correctly identify the beneficiary of the swap regardless of which intermediary called the pool.

Either way, the extension's NatSpec and the pool admin documentation must clearly state which actor is gated so that admins configure the allowlist correctly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        recipient: attacker,
        ...
    })
  - Router calls pool.swap(attacker, zeroForOne, amount, ...)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens.

Result:
  - attacker, who is not on the per-user allowlist, successfully swaps
    against the curated pool, bypassing the intended access control.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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
