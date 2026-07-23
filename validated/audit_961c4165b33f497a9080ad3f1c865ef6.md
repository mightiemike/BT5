Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Original Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` at the pool level — the router contract address when a swap is routed through `MetricOmmSimpleRouter`. Because the router does not forward the original caller's identity into `extensionData` or any other pool-visible field, the extension cannot distinguish the router (infrastructure) from the economic actor (user). A pool admin who allowlists the router to restore router usability for approved users inadvertently opens the pool to every caller of the router, completely defeating the allowlist.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`:**

`MetricOmmPool.swap` at L230–240 calls `_beforeSwap(msg.sender, ...)`. When invoked via `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards `sender` unchanged:**

`ExtensionCalling._beforeSwap` at L160–176 encodes `sender` directly into the extension call with no transformation. [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` — the router address:**

At L37, `msg.sender` is the pool and `sender` is the router. The lookup `allowedSwapper[pool][router]` gates the router contract, not the originating user. [3](#0-2) 

**`MetricOmmSimpleRouter` does not forward the original caller's identity:**

`exactInputSingle` at L71–80 stores `msg.sender` only in the transient callback context (for payment), but calls `pool.swap(...)` with `extensionData` taken verbatim from `params.extensionData` — the original user's address is never encoded into any field visible to the extension. [4](#0-3) 

The same pattern applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181). [5](#0-4) 

**Two mutually exclusive failure modes with no safe configuration:**

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router — the supported periphery path is broken |
| Router **allowlisted** (to fix the above) | Every user, including those explicitly denied, bypasses the allowlist via the router |

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for KYC, risk-gating, or protocol-restricted liquidity loses its access-control guarantee the moment the admin allowlists the router to restore router usability for approved users. Any unprivileged address can then call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on the restricted pool. This is an admin-boundary break: an admin-configured access control is bypassed by an unprivileged path (the public router), exposing LP assets to unauthorized counterparties and causing the pool's curation invariant to fail.

## Likelihood Explanation
The trigger path follows a natural operational sequence that any pool admin would execute: deploy curated pool → approved users fail on router → admin allowlists router → all users bypass allowlist. Step 3 is the obvious remediation. The vulnerability is latent in the design and activates on a routine, expected admin action. No special attacker capability is required beyond calling the public router.

## Recommendation
The `SwapAllowlistExtension` must gate on the original user, not the immediate pool caller. Two approaches:

1. **Encode original caller in `extensionData` at the router**: Modify `MetricOmmSimpleRouter` to prepend the original `msg.sender` into `extensionData` before calling `pool.swap`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.

2. **Redefine `sender` semantics at the router level**: Require the router to always forward the original caller identity as the `sender`-equivalent field, and update the extension to use that value exclusively for allowlist checks.

Either fix must ensure the extension can distinguish the router (infrastructure) from the user (economic actor) and apply the allowlist to the latter.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is approved)
  allowedSwapper[pool][bob]   = false  (bob is denied)

Step 1 — alice tries router, fails:
  alice → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  pool.swap(msg.sender=router, ...)
  _beforeSwap(sender=router) → allowedSwapper[pool][router] = false → NotAllowedToSwap ✗

Step 2 — admin allowlists router to fix alice's access:
  admin → setAllowedToSwap(pool, router, true)

Step 3 — bob bypasses allowlist:
  bob → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  pool.swap(msg.sender=router, ...)
  _beforeSwap(sender=router) → allowedSwapper[pool][router] = true → passes ✓
  bob executes swap on restricted pool despite being explicitly denied
```

Foundry test: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][alice]=true`, confirm alice's direct call succeeds and bob's direct call reverts, then `setAllowedToSwap(pool, router, true)`, confirm bob's router call now succeeds — demonstrating full allowlist bypass.

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
