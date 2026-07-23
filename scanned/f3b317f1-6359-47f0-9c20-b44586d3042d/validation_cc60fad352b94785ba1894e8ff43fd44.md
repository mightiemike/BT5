### Title
`SwapAllowlistExtension` checks the router address as the swapper instead of the actual user, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two mutually exclusive failure modes: if the router is allowlisted, every user on the network can bypass the per-user allowlist; if the router is not allowlisted, every allowlisted user is silently blocked from using the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol line 163-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then uses that first argument as the identity to gate:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

At the pool, `msg.sender = router`, so `sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path (router allowlisted):** A pool admin who wants to support router-mediated swaps must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] = true` passes for every user regardless of their individual allowlist status. Any non-allowlisted user calls `router.exactInputSingle(pool, ...)` and the guard passes.

**Broken path (router not allowlisted):** If the pool admin allowlists individual EOAs but not the router, those EOAs cannot use the router at all — the extension reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] = false`.

---

### Impact Explanation

**Allowlist bypass (High):** A curated pool (e.g., KYC-only, market-maker-only) that relies on `SwapAllowlistExtension` to restrict trading to approved counterparties is fully bypassed by any user routing through `MetricOmmSimpleRouter`. Non-approved users can execute swaps against the pool's oracle-anchored pricing, draining LP value or exploiting any pricing advantage the pool was designed to reserve for approved parties.

**Broken core functionality (Medium):** If the router is not allowlisted, allowlisted users cannot use the standard periphery swap path. All multi-hop routing, exact-output routing, and WETH-unwrap flows become inaccessible to the very users the pool was configured to serve.

Both outcomes are contest-relevant: the first is a direct admin-boundary break reachable by any unprivileged user; the second is broken core swap functionality.

---

### Likelihood Explanation

Any pool that deploys with `SwapAllowlistExtension` and expects users to route through `MetricOmmSimpleRouter` hits one of the two failure modes immediately. The router is the primary public swap entrypoint documented in the periphery. The misconfiguration is not hypothetical — it is the only stable operating point for a curated pool that also wants router support.

---

### Recommendation

The extension must gate on the **economic actor** (the user who initiated the transaction), not the **proximate caller** of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. This requires a trusted router or a signed attestation.

2. **Check `recipient` instead of `sender` for router flows:** The router sets `recipient` to the user-supplied address; however, `recipient` is also user-controlled and not a reliable identity anchor.

3. **Preferred — gate at the router level:** Add a separate `RouterSwapAllowlistExtension` that the router calls before forwarding, or have the router enforce the allowlist on behalf of the pool using a signed permit from the pool admin.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at pool creation (e.g., reject the combination in `validateExtensionsConfig` or in the router itself).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Non-allowlisted user `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. `beforeSwap` checks `allowedSwapper[pool][router] = true` → passes.
6. `attacker` successfully swaps on the curated pool despite never being allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
