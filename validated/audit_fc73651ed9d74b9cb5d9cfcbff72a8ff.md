Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is the `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. A pool admin who allowlists the router to enable router-mediated swaps on a curated pool inadvertently grants every user — including those never individually allowlisted — unrestricted access to the pool.

## Finding Description
The call chain is fully confirmed in production code:

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`:**
`MetricOmmPool.swap` at L230–240 calls `_beforeSwap(msg.sender, ...)`. When the router is the caller, `msg.sender` is the router address. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:**
At L160–176, `sender` is passed directly into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` with no substitution. [2](#0-1) 

**Step 3 — Extension checks the router address, not the end-user:**
`SwapAllowlistExtension.beforeSwap` at L37 evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router. The end-user identity is never consulted. [3](#0-2) 

**Step 4 — Router never forwards the original caller to the pool:**
`MetricOmmSimpleRouter.exactInputSingle` at L71–80 stores the original `msg.sender` only in transient storage via `_setNextCallbackContext` for the payment callback. The `pool.swap(...)` call receives no information about the original caller. [4](#0-3) 

The root cause is a mismatch between the identity the extension checks (`sender` = immediate caller of `pool.swap`) and the identity the allowlist is intended to gate (the economic end-user). No existing guard in the pool, extension, or router corrects this mismatch.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` faces two broken outcomes:

1. **Allowlist bypass (high impact):** If the admin allowlists the router so that router-mediated swaps are possible, every user — including those never individually allowlisted — can swap freely through the router. The per-user curation is completely defeated. Unauthorized users gain access to a pool that may have privileged pricing, LP-subsidized rates, or restricted counterparty requirements, constituting a direct policy bypass with fund-impacting consequences.

2. **Broken core functionality (medium impact):** If the admin allowlists only specific user addresses (not the router), those allowlisted users cannot swap through the router at all, because the check sees the router address and rejects it. The standard swap UX is broken for legitimate users.

This matches the "Allowlist path" audit pivot: swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through the router.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the standard, documented swap entrypoint for end-users.
- Any user can call it permissionlessly with no special preconditions.
- A pool admin enabling router-mediated swaps on a curated pool is the expected operational pattern.
- No flash loans, multi-block timing, or privileged access are required — a single `exactInputSingle` call suffices.

## Recommendation
The extension must receive and check the original end-user identity, not the immediate caller of `pool.swap()`. The cleanest fix is to define a standard extension-data envelope that the router populates with the original caller (`msg.sender`), and have `SwapAllowlistExtension.beforeSwap` decode and verify that field when present. Alternatively, the router could forward the original caller as a dedicated parameter if the protocol adds a convention for it. Checking `recipient` instead of `sender` is only safe if the pool guarantees `recipient == end-user`, which is not enforced today.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin allowlists the router: `swapExtension.setAllowedToSwap(pool, address(router), true)`.
3. Non-allowlisted user `alice` calls `router.exactInputSingle({pool: pool, recipient: alice, ...})`.
4. Router executes `_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, alice, tokenIn)` (transient only), then calls `pool.swap(alice, ...)` — `msg.sender` to the pool is the router.
5. Pool calls `_beforeSwap(router, alice, ...)` → extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `alice` successfully swaps on the curated pool despite never being individually allowlisted.

A Foundry integration test can reproduce this by deploying the pool with the extension, calling `setAllowedToSwap(pool, router, true)`, then calling `router.exactInputSingle` from an address not in `allowedSwapper`, and asserting the swap succeeds.

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
