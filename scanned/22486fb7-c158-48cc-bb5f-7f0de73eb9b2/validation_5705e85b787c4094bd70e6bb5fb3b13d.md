### Title
`SwapAllowlistExtension` Checks Router Identity Instead of User Identity, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (a natural action for a trusted periphery), every user — including those explicitly excluded from the allowlist — can bypass the swap guard by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is whatever the pool passes as the first argument to the extension callback.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
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

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who allowlists the router address — treating it as a trusted periphery — inadvertently grants every user the ability to bypass the allowlist by routing through `MetricOmmSimpleRouter`.

The same identity substitution occurs in `exactInput` (all hops call `pool.swap()` with `msg.sender = router`) and in the recursive `_exactOutputIterateCallback` path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified users, protocol-internal actors, or a closed beta group) is completely unprotected once the router is allowlisted. Any address can execute swaps against the restricted pool by calling `MetricOmmSimpleRouter`, receiving the full economic output of the swap. This breaks the core access-control invariant the extension is designed to enforce and constitutes a direct bypass of an admin-configured security boundary with fund-impacting consequences (non-permitted parties trade against LP capital).

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be active. This is a plausible and natural administrative action: the router is a trusted, audited periphery contract, and an admin may allowlist it to allow "the protocol's own router" to route swaps without realizing that doing so grants access to every user who calls the router. The admin has no on-chain signal that allowlisting the router is semantically different from allowlisting a specific EOA. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any user can trigger it by calling a public router function.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward the actual `msg.sender` (the user) to the pool as an additional field (e.g., in `extensionData`), and the extension should decode and check that address. This requires a coordinated change to the router and extension.

2. **Check `sender` only when it equals the pool's `msg.sender`.** Alternatively, the extension can require that `sender == msg.sender` (i.e., direct calls only), rejecting any intermediary path. This is simpler but prevents legitimate router use.

The cleanest production fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it, falling back to `sender` only when `extensionData` is empty (direct pool calls).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (allowlisting the router as a trusted periphery).
  - Pool admin does NOT allowlist alice (alice is a non-permitted user).

Attack:
  1. alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(...); pool's msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. alice's swap executes successfully despite not being on the allowlist.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; alice receives output tokens from LP capital.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
