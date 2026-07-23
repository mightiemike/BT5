Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address against the allowlist rather than the actual end-user. If the pool admin allowlists the router — a natural action to enable router-mediated swaps for permitted users — every user who routes through the router bypasses the allowlist entirely, defeating the purpose of the extension.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

This makes the router the `msg.sender` to the pool, so `sender` in `beforeSwap` is the router address. The check becomes `allowedSwapper[pool][router]`. There is no mechanism in the call path to carry the original end-user identity through the router hop — `extensionData` is forwarded as-is from the user's call but the `sender` binding is fixed at the pool level to `msg.sender`.

If the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for permitted users, the check `allowedSwapper[pool][router] == true` passes for **all** users who route through the router, regardless of whether they are individually allowlisted.

## Impact Explanation
Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose — restricting swaps to specific permitted addresses (e.g., KYC-gated, institutional-only, or risk-controlled pools) — is completely defeated. Unauthorized users gain full swap access to pools intended to be restricted. This constitutes broken core pool functionality causing a policy bypass on curated pools.

## Likelihood Explanation
The pool admin must allowlist the router for this to be exploitable. This is a natural and expected action: if any allowlisted user wants to use the router (for multi-hop swaps, slippage protection, or permit-based flows via `selfPermit`), the admin must allowlist the router. The admin has no mechanism to allowlist the router for specific users only — allowlisting the router opens the gate to all users. The vulnerability is therefore reachable through normal, non-malicious pool administration. The `setAllowedToSwap` setter is permissionless to call by the pool admin with no timelock or cap: [5](#0-4) 

## Recommendation
The `SwapAllowlistExtension` should check the actual end-user identity rather than the direct caller of `swap()`. Options:
1. **`extensionData` attestation**: Require the actual user address to be passed in `extensionData` (signed or attested by the router), and verify it against the allowlist inside `beforeSwap`.
2. **Router-aware extension**: Have the router pass the originating user address in `extensionData`, and update `SwapAllowlistExtension` to decode and check that address when `sender` is a known router.
3. **Documentation guard**: Explicitly document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this breaks the router UX for curated pools.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — allowlists user A.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlists the router so user A can use it.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is the router.
6. Pool calls `_beforeSwap(router, ...)` → extension's `beforeSwap(sender=router, ...)` is called.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. User B successfully swaps on the allowlisted pool, bypassing the allowlist entirely.

A Foundry test can reproduce this by deploying the pool with `SwapAllowlistExtension`, allowlisting the router, and asserting that an unallowlisted user's router call succeeds where a direct pool call would revert.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
