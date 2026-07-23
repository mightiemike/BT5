### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, not the end user. A pool admin who allowlists the router address (the only way to permit allowlisted users to trade through the standard periphery) inadvertently opens the gate to every user who calls the router, defeating the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` of `pool.swap()` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` of `pool.swap()` is the **router contract**, so `sender` seen by the extension is the router address, not the originating user. The extension then evaluates `allowedSwapper[pool][router]`.

**The trap:** A pool admin who wants allowlisted users to trade through the standard periphery must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the actual end user is. Any non-allowlisted address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the guard passes.

The same problem applies to the multi-hop `exactInput` path, where the router is `msg.sender` for every intermediate hop:

```solidity
// MetricOmmSimpleRouter.sol L103-112
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        ...
    );
```

There is no mechanism in the router to forward the originating user's identity to the pool or to the extension.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist becomes a direct-call-only gate that the standard periphery silently bypasses. Depending on the pool's purpose, this can result in:

- Unauthorized users executing swaps and extracting value from LP positions that were priced for a restricted counterparty set.
- Protocol-fee or LP-asset loss if the pool's pricing model assumes only vetted counterparties trade against it.
- Complete failure of the curation invariant the pool admin configured.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user with no special setup. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any pool that wants its allowlisted users to trade through the standard periphery rather than calling the pool directly. The `MetricOmmSimpleRouter` is a public, permissionless contract. No privileged access, malicious token, or non-standard ERC-20 is required.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router.** The router should encode the real `msg.sender` into `extensionData` (or a dedicated field) and the extension should decode and verify it. This requires a trust assumption that the router is the only allowed intermediary, which can be enforced by checking `sender == trustedRouter` before accepting the decoded identity.

2. **Check `recipient` instead of `sender` for swap allowlisting.** If the pool's intent is to restrict who *receives* swap output, `recipient` is the correct field. If the intent is to restrict who *initiates* the swap, the router indirection must be resolved as in option 1.

3. **Document that the allowlist only gates direct pool calls.** If router bypass is acceptable, the `SwapAllowlistExtension` NatSpec and pool admin tooling must clearly state that routing through `MetricOmmSimpleRouter` is not gated, so admins do not deploy it under a false security assumption.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can trade through the standard periphery.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes against the curated pool with no allowlist enforcement.

Relevant code path: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
