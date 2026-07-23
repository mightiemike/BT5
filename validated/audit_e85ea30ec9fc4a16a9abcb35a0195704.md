Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of real swapper, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is always `msg.sender` of the `pool.swap` call. When swaps route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of the real user's address. This produces two fund-impacting failure modes: allowlisted users are permanently blocked from using the router, and allowlisting the router to fix that silently opens the pool to every address on-chain.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the pool see `msg.sender = router`. The real user's address is stored only in transient callback context (`_setNextCallbackContext`) and is never forwarded to the extension: [3](#0-2) 

The same wrong-actor binding applies to `exactOutputSingle`, `exactInput`, and `exactOutput`, all of which call `pool.swap` with the router as `msg.sender`: [4](#0-3) 

The `setAllowedToSwap` setter accepts any address with no warning that allowlisting the router address opens the gate to all callers: [5](#0-4) 

No existing guard in the extension or pool checks the original EOA initiator. The `extensionData` field passed through the call chain is user-supplied and unauthenticated, so it cannot serve as a trust anchor without router-side attestation.

## Impact Explanation
**Mode A — Broken core swap flow:** A pool admin allowlists specific users (e.g., KYC addresses). Those users call `exactInputSingle` through the router. The extension sees `sender = router`, which is not allowlisted, and reverts with `NotAllowedToSwap`. The curated pool is permanently unusable for its intended participants through the only supported periphery path — a broken core swap flow causing loss of access to funds.

**Mode B — Complete allowlist bypass:** To restore router access, the admin calls `setAllowedToSwap(pool, router, true)`. Any unprivileged user now calls `exactInputSingle` through the router; the extension sees `sender = router`, which is allowlisted, and passes. The allowlist is entirely nullified for all router-mediated swaps — an admin-boundary break where an unprivileged trader bypasses a per-user swap gate.

## Likelihood Explanation
Mode A is triggered by any allowlisted user attempting to use the router — a normal, expected action. Mode B is triggered by the pool admin taking the natural remediation step of allowlisting the router after discovering Mode A. The `setAllowedToSwap` setter emits only a generic `AllowedToSwapSet` event with no warning, and the router is a factory-registered, protocol-endorsed contract, making it a plausible allowlist entry. No special attacker capability is required; any on-chain address can exploit Mode B once the admin performs the remediation.

## Recommendation
The extension must gate the economically relevant actor — the human initiating the swap — not the intermediary contract. Two viable approaches:

1. **Extension-data forwarding:** The router encodes the original `msg.sender` into `extensionData` for each hop. The extension decodes and checks that address instead of `sender`. The router already accepts per-hop `extensionData` from callers.

2. **Trusted-router attestation:** The extension maintains a registry of trusted router addresses. When `sender` is a trusted router, the extension decodes the real initiator from `extensionData` and verifies the attestation came from that trusted router before checking the allowlist.

Until fixed, `SwapAllowlistExtension` must be documented as incompatible with `MetricOmmSimpleRouter` for pools that require per-user swap gating.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  setAllowedToSwap(pool, alice, true)   // only Alice is allowed

Step 1 — Mode A (broken flow):
  alice calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...) [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension: allowedSwapper[pool][router] == false → revert NotAllowedToSwap
  Alice cannot use the router at all.

Step 2 — Admin remediation (natural but fatal):
  admin calls setAllowedToSwap(pool, router, true)

Step 3 — Mode B (bypass):
  bob (never allowlisted) calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...) [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension: allowedSwapper[pool][router] == true → passes
  Bob swaps freely on the curated pool.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
