The code confirms all three legs of the claim. Let me verify the complete call chain:

**Leg 1 — Extension check** (`SwapAllowlistExtension.sol` line 37): `sender` is the first argument to `beforeSwap`, and `msg.sender` is the pool. [1](#0-0) 

**Leg 2 — Pool passes its own `msg.sender` as `sender`** (`MetricOmmPool.sol` lines 230–231): [2](#0-1) 

**Leg 3 — Router is the direct caller of `pool.swap`** (`MetricOmmSimpleRouter.sol` lines 72–80): [3](#0-2) 

The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) — all call `pool.swap` with `msg.sender = router`. [4](#0-3) 

The bypass path is fully confirmed by production code. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the direct pool caller (`sender`), not the originating user, enabling full allowlist bypass for any caller when the router is allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router contract address. If the pool admin allowlists the router — the only available mechanism to permit router-mediated swaps for allowlisted users — every unprivileged caller of the router bypasses the allowlist entirely, because the originating user identity is never forwarded to the extension.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(msg.sender, recipient, zeroForOne, amountSpecified, ...);
```

`ExtensionCalling._beforeSwap` forwards this value as `sender` to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap`, the router is `msg.sender`, so `sender = router`. The check becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router has no per-user mechanism: the only option is `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every caller of any router entry point passes the check unconditionally. The originating EOA identity is never available to the extension and cannot be recovered from call context.

Existing guards are insufficient: `BaseMetricExtension.onlyPool` only verifies the extension is called by a registered pool, not that the `sender` argument reflects the true originating user.

## Impact Explanation
Any unprivileged user can bypass `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The core access-control invariant — only allowlisted addresses may swap in a restricted pool — is broken. Unauthorized users can trade in pools intended to be gated (KYC-only, institutional-only, protocol-internal), extracting value from LP positions whose owners deposited under the assumption that the allowlist was enforced. This is broken core pool functionality with direct fund impact on LPs in restricted pools.

## Likelihood Explanation
Requires the pool admin to have allowlisted the router. This is a natural and expected configuration: a pool admin who wants allowlisted users to benefit from router UX or multi-hop routing must add the router to the allowlist — it is the only mechanism available. The admin has no way to express "allow specific users through the router"; allowlisting the router silently opens the gate to all router callers. The precondition is reachable by any pool admin operating a restricted pool with router support.

## Recommendation
The extension must gate on the originating user, not the intermediary contract. Two viable approaches:

1. **Pass originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and verifies it. Requires a trusted-router convention and documentation.
2. **Router-aware extension with signed permit**: Provide a separate extension variant that accepts a signed permit from the originating user embedded in `extensionData`, allowing per-user gating through any intermediary without trusting the router to self-report identity.

Until fixed, document explicitly that allowlisting the router opens the gate to all router callers, and advise pool admins not to allowlist the router if per-user gating is required.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist a specific user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it
4. Attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender = router
6. SwapAllowlistExtension.beforeSwap receives sender = router
7. allowedSwapper[pool][router] == true → check passes
8. Attacker's swap executes in the restricted pool without being allowlisted
```

Foundry test: deploy pool + SwapAllowlistExtension, allowlist only `userA` and the router, call `exactInputSingle` from an unallowlisted address, assert the swap succeeds (no `NotAllowedToSwap` revert).

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
