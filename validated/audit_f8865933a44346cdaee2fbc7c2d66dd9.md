Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. Allowlisting the router (required for any router-mediated swap to succeed) simultaneously opens the gate to every user on the network, rendering the per-user allowlist completely ineffective.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to embed the original caller's identity: [4](#0-3) 

This means `msg.sender` inside `pool.swap()` is the router, not the original user. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin does not allowlist the router, all router-mediated swaps revert even for allowlisted users. If the pool admin does allowlist the router (the only way to enable router-mediated swaps for legitimate users), every user on the network can bypass the per-user allowlist by calling through the router. The `extensionData` field passed from the router is user-controlled and trivially forgeable, so there is no existing guard that can recover the real caller identity.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is a curated pool intended to restrict trading to a specific set of addresses. Once the router is allowlisted, the allowlist is completely ineffective: any address can trade by routing through `MetricOmmSimpleRouter`. This is a direct, unprivileged bypass of the pool's access-control policy — broken core pool functionality causing the access-control invariant to fail entirely, with no additional preconditions beyond knowing the router address.

## Likelihood Explanation
The router is the standard, documented periphery entry point for swaps. Any user who reads the protocol documentation or inspects on-chain transactions will discover it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only a single public function call to the router. The attack is repeatable and unconditional once the router is allowlisted.

## Recommendation
The `sender` argument forwarded to extensions must represent the original user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side:** Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` in a tamper-evident, authenticated way (e.g., a dedicated ABI-encoded prefix that the extension can decode and verify came from a trusted router).
2. **Extension-side:** `SwapAllowlistExtension` should accept a trusted-router registry and, when `sender` is a known router, extract and verify the real user from `extensionData` before performing the allowlist lookup.

Alternatively, document clearly that `SwapAllowlistExtension` only gates direct pool calls and that router-mediated swaps are always unrestricted, and rename/redesign accordingly.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // required for any router swap
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  bob is NOT in the allowlist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  pool.swap() is called with msg.sender = router
  _beforeSwap(router, ...) is dispatched
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  bob's swap executes successfully despite not being allowlisted
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
