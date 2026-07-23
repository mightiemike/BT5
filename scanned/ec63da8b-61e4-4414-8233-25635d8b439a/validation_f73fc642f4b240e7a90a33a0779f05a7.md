### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Blocking Allowlisted Users and Enabling Full Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the user. The extension therefore checks whether the **router** is allowlisted, not the actual trader. This creates two mutually exclusive failure modes: (1) allowlisted users are silently blocked from using the router, and (2) if the pool admin adds the router to the allowlist to restore router access, every non-allowlisted user can bypass the guard by routing through the same public contract.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to every extension hook.** [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, `msg.sender` inside the pool is the router contract address. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The mapping lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` — the actual position owner passed explicitly by the caller — rather than `sender`: [4](#0-3) 

The asymmetry is structural: `addLiquidity` separates payer (`msg.sender`) from owner (explicit argument), so the deposit extension can gate the right identity. `swap` has no equivalent "actual user" argument; the pool only forwards `msg.sender`.

**Two reachable failure modes:**

| Mode | Trigger | Effect |
|---|---|---|
| **Block** | Allowlisted user calls router; router not in allowlist | `NotAllowedToSwap` — allowlisted user cannot trade via router |
| **Bypass** | Admin adds router to allowlist to restore router access | Any non-allowlisted user calls router → `allowedSwapper[pool][router]` = true → swap succeeds |

---

### Impact Explanation

**Mode 1 (Block):** Allowlisted users are unable to use `MetricOmmSimpleRouter` for single-hop or multi-hop swaps. The router is the primary user-facing swap interface. Affected users must call `pool.swap` directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves — a capability not available to EOAs or standard wallets. This renders the swap flow unusable for the intended user population.

**Mode 2 (Bypass):** The only operational fix available to the pool admin (adding the router to the allowlist) opens the gate to every address that can call the public router. If the allowlist exists to restrict trading to KYC'd counterparties, institutional participants, or to protect LP positions from adversarial flow, the bypass allows unrestricted trading against LP funds, directly threatening LP principal.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to trade through `MetricOmmSimpleRouter` hits Mode 1 immediately on the first router call. Mode 2 is triggered the moment the admin attempts the natural remediation. Both modes require only a standard public call to the router — no special privileges, no malicious setup.

---

### Recommendation

The `SwapAllowlistExtension` should gate the **original user**, not the intermediary. Two sound approaches:

1. **Decode user from `extensionData`:** The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. The router's identity is verified via `onlyPool` on the extension side, so the extension can trust the payload only when the caller is a known pool.

2. **Allowlist the router with a per-user sub-check:** The extension accepts the router as a trusted forwarder and requires the router to attest the real user in `extensionData`, similar to ERC-2771 meta-transaction patterns.

The deposit extension's pattern — checking an explicit `owner` argument rather than `sender` — is the correct model. The swap path needs an equivalent explicit user identity that survives router intermediation.

---

### Proof of Concept

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

The root cause is in `SwapAllowlistExtension.beforeSwap` at [5](#0-4)  checking `sender` (the router) rather than the originating user, while the pool unconditionally passes `msg.sender` as `sender` at [6](#0-5) .

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
