### Title
`SwapAllowlistExtension` gates on the router address instead of the originating user, enabling allowlist bypass for any caller — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. A pool admin who allowlists the router to support router-based swaps inadvertently opens the gate to every user, completely defeating the per-user curation policy.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same substitution occurs for `exactInput` intermediate hops (payer switches to `address(this)` but the pool's `msg.sender` is still the router): [5](#0-4) 

And for `exactOutput` recursive hops inside `_exactOutputIterateCallback`, the router again calls `pool.swap(...)`, so the pool still sees `msg.sender = router`: [6](#0-5) 

The `DepositAllowlistExtension` does **not** share this flaw because it ignores the `sender` parameter entirely and gates on `owner` (the position owner), which the liquidity adder passes through correctly. The swap extension has no equivalent owner-level identity to fall back on. [7](#0-6) 

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that uses `SwapAllowlistExtension`:

**Mode A — allowlist bypass (fund impact):** A pool admin who wants allowlisted users to be able to trade through the router must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true` and the guard passes for every caller regardless of their individual allowlist status. Any unprivileged user can call `exactInputSingle` or `exactInput` and execute swaps on a pool that was intended to be restricted to specific counterparties. This breaks the curation invariant and can expose LP funds to unintended counterparties (e.g., non-KYC'd users on a regulated pool, or adversarial traders on a pool designed for a closed market-making arrangement).

**Mode B — broken core functionality:** If the admin does not allowlist the router, individually allowlisted users cannot use the router at all — every router-mediated swap reverts with `NotAllowedToSwap`. The router is the protocol's primary supported swap entrypoint, so this renders the pool's swap functionality broken for its intended user base.

Both outcomes are contest-relevant: Mode A is a direct policy bypass with potential loss of LP principal to unintended counterparties; Mode B is broken core pool functionality.

---

### Likelihood Explanation

The trigger requires no privileged action beyond what a pool admin would naturally do. A pool admin deploying a curated pool with `SwapAllowlistExtension` and wanting to support the standard router path will allowlist the router — this is the obvious configuration step. The bypass is then reachable by any unprivileged user with zero additional preconditions. The router is a public, permissionless contract.

---

### Recommendation

The extension must recover the originating user identity rather than relying on the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `sender` against the router and require a secondary allowlist entry for the true originator:** The extension recognises known router addresses and reads the originating user from a router-provided field in `extensionData`, falling back to `sender` for direct pool calls.

The `DepositAllowlistExtension` pattern of gating on `owner` (a pool-level identity that survives intermediary routing) is the correct model; the swap path lacks an equivalent owner-level field and must be retrofitted.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the intended gated user
  allowedSwapper[pool][router] = true  // admin adds router to support alice's router usage

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle
      → pool.swap(recipient=bob, ...)          // msg.sender in pool = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (guard passes)
        → swap executes, bob receives tokens

Result:
  bob successfully swaps on a pool that was intended to restrict swaps to alice only.
  The allowlist is completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
