Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the originating user, allowing any caller to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the allowlist gate to every unprivileged address that calls through the router.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` parameter in the ABI-encoded call to every registered extension.

**Step 2 — Extension checks that value against the allowlist.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router, not the originating user.

**Step 3 — Router is the immediate caller of `pool.swap()`.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router calls `pool.swap()` directly with no forwarding of the originating user. `msg.sender` inside the pool is therefore the router address, so `sender` forwarded to the extension is the router address. The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165).

**Step 4 — Bypass.**

A pool admin who wants to allow router-mediated swaps for allowlisted users has no mechanism to do so other than allowlisting the router itself:

```
swapExtension.setAllowedToSwap(pool, address(router), true);
```

Once set, `allowedSwapper[pool][router] == true` for every call arriving through the router, regardless of the originating EOA. Any non-allowlisted address can call `router.exactInputSingle(...)` and the extension passes unconditionally.

**Existing guards are insufficient.** The `allowAllSwappers` flag is a separate escape hatch. The `allowedSwapper` mapping is keyed by `(pool, sender)` where `sender` is always the router for router-mediated swaps. There is no mechanism in the extension or the router to propagate the originating user's identity into the allowlist check.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses) is fully open to any caller the moment the pool admin allowlists the router. This constitutes broken core pool functionality — the allowlist guard fails open — and an admin-boundary break where an unprivileged path bypasses the pool admin's intended access control. LP capital deposited under the assumption that only vetted counterparties would trade is exposed to arbitrary swappers executing at oracle-derived prices.

## Likelihood Explanation

The precondition — the router being allowlisted — is a natural and expected operational step. Any pool that wants to support the standard periphery flow for its allowlisted users must grant the router entry, since the router is always the immediate `msg.sender` of `pool.swap()`. The design gives the admin no way to simultaneously allow router-mediated swaps for specific users and block them for others. The bypass is therefore an inevitable consequence of enabling router support on a curated pool, not an exotic edge case.

## Recommendation

The extension must gate on the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated change in the router and the extension.

2. **Trusted-forwarder registry.** The extension maintains a registry of trusted forwarder contracts (e.g., the router). When `sender` is a registered forwarder, the extension requires the originating address to be ABI-encoded in `extensionData` and checks that address instead. When `sender` is not a forwarder, it checks `sender` directly as today.

Either way, the extension must not treat the router address as the identity to gate.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended gated user
  allowedSwapper[pool][router] = true         // admin enables router for alice's convenience

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
        pool:      pool,
        recipient: bob,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  Execution trace:
    router.exactInputSingle()                          // MetricOmmSimpleRouter.sol L67
      → pool.swap(msg.sender = router)                 // MetricOmmPool.sol L217
        → _beforeSwap(sender = router, ...)            // MetricOmmPool.sol L230
          → SwapAllowlistExtension.beforeSwap(sender = router)  // SwapAllowlistExtension.sol L31
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, bob receives output tokens

Result: bob swaps successfully despite never being allowlisted.
```

A Foundry integration test can reproduce this by deploying a pool with `SwapAllowlistExtension`, allowlisting only alice and the router, then calling `router.exactInputSingle` from an address that is neither alice nor the router and asserting the swap succeeds.