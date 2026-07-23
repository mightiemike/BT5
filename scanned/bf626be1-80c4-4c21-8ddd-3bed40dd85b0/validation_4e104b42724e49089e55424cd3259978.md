### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any caller to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is intended to gate swaps on a per-user basis. However, when a swap is routed through `MetricOmmSimpleRouter`, the extension receives the **router's address** as the `sender` argument rather than the actual end-user's address. If the pool admin allowlists the router (a natural configuration for a pool that wants to support router-mediated swaps), the allowlist is completely ineffective: any unprivileged user can bypass it by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`.

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The `msg.sender` of `pool.swap()` is the **router contract**, not the end-user. Therefore the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The same applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: in every case the router is the direct caller of `pool.swap()`.

**Bypass path**: A pool admin who wants to support router-mediated swaps for allowlisted users will allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the `beforeSwap` check passes for **every** caller who routes through the router, regardless of whether that caller is individually allowlisted. The allowlist is rendered inoperative.

**Broken-functionality path** (secondary): If the pool admin does NOT allowlist the router, then individually allowlisted users who attempt to swap through the router will be blocked, because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled addresses) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against the pool. This constitutes a direct bypass of an access-control guard with fund-impacting consequences: unauthorized parties can drain LP-owned assets at oracle-derived prices, and the pool admin's curation policy is silently nullified.

---

### Likelihood Explanation

The router is the canonical public entry point for swaps. A pool admin who configures a `SwapAllowlistExtension` and also wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router. The bypass is then reachable by any user with no special privileges, no malicious setup, and no non-standard tokens. The trigger is a standard `exactInputSingle` call.

---

### Recommendation

The extension must identify the **economic actor** (the end-user), not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: Have the router forward `msg.sender` as an explicit `sender` field inside `extensionData`, and have the extension decode and verify it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender`** (partial mitigation only — does not work for all cases): Not recommended as a general fix since `recipient` is also router-controlled.

3. **Preferred**: Require that direct pool calls (not router-mediated) are the only path for allowlisted pools, and document this constraint clearly. Alternatively, redesign the extension to accept a signed user-identity proof inside `extensionData` that the router populates from `msg.sender` before forwarding.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not individually allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: attacker, ...})
  - Router calls pool.swap(attacker, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Swap executes; attacker receives pool tokens
  - allowedSwapper[pool][attacker] was never set; the guard was bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
