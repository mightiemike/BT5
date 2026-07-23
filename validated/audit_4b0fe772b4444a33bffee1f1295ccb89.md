Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Blocking Allowlisted Users and Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the `msg.sender` of the pool call, which is the router contract when users trade through `MetricOmmSimpleRouter`. The check `allowedSwapper[msg.sender][sender]` therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. This produces two mutually exclusive failure modes: allowlisted users are blocked from using the router, and if the admin adds the router to the allowlist to restore access, every non-allowlisted address can bypass the guard.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, `msg.sender` inside the pool is the router contract address, not the originating user: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The mapping lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — an explicit argument representing the actual position owner — rather than `sender`: [4](#0-3) 

The asymmetry is structural: `swap` has no equivalent explicit "actual user" argument; the pool only forwards `msg.sender` as `sender`.

**Two reachable failure modes:**

| Mode | Trigger | Effect |
|---|---|---|
| **Block** | Allowlisted user calls router; router not in allowlist | `NotAllowedToSwap` — allowlisted user cannot trade via router |
| **Bypass** | Admin adds router to allowlist to restore router access | Any non-allowlisted user calls router → `allowedSwapper[pool][router]` = true → swap succeeds |

## Impact Explanation
**Mode 1 (Block):** Allowlisted users cannot use `MetricOmmSimpleRouter` for any swap variant. The router is the primary user-facing swap interface. Direct `pool.swap` calls require implementing `IMetricOmmSwapCallback`, which is not available to EOAs or standard wallets, making the swap flow entirely unusable for the intended user population — broken core pool functionality.

**Mode 2 (Bypass):** The only operational remediation available to the pool admin (adding the router to the allowlist) opens the gate to every address that can call the public router. If the allowlist exists to restrict trading to KYC'd counterparties or to protect LP positions from adversarial flow, the bypass allows unrestricted trading against LP funds, directly threatening LP principal — a direct loss-of-funds risk for LPs.

## Likelihood Explanation
Any pool deploying `SwapAllowlistExtension` and expecting users to trade through `MetricOmmSimpleRouter` hits Mode 1 immediately on the first router call. Mode 2 is triggered the moment the admin attempts the natural remediation. Both modes require only a standard public call to the router — no special privileges, no malicious setup, no non-standard token behavior.

## Recommendation
The extension must gate the **originating user**, not the intermediary. Two sound approaches:

1. **Decode user from `extensionData`:** The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. The router's identity is verified via `msg.sender == pool` on the extension side, so the extension can trust the payload only when the caller is a known pool.

2. **ERC-2771-style trusted forwarder:** The extension accepts the router as a trusted forwarder and requires the router to attest the real user in `extensionData`, similar to meta-transaction patterns.

The deposit extension's pattern — checking an explicit `owner` argument rather than `sender` — is the correct model. The swap path needs an equivalent explicit user identity that survives router intermediation.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is allowlisted
  allowedSwapper[pool][router] = false // router is not

Attack (Mode 1 — Block):
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...) with msg.sender = router
  pool calls extension.beforeSwap(router, ...)
  extension checks allowedSwapper[pool][router] → false
  → revert NotAllowedToSwap
  alice cannot trade despite being allowlisted

Remediation attempt:
  admin sets allowedSwapper[pool][router] = true

Attack (Mode 2 — Bypass):
  bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...) with msg.sender = router
  pool calls extension.beforeSwap(router, ...)
  extension checks allowedSwapper[pool][router] → true
  → swap succeeds
  bob bypasses the allowlist entirely
```

Foundry test: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][alice] = true`, call `router.exactInputSingle` as alice, assert revert. Then set `allowedSwapper[pool][router] = true`, call as bob (not allowlisted), assert swap succeeds.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
