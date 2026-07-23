### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Per-User Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the `sender` forwarded to the extension is the **router's address**, not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for allowlisted users), every unprivileged user can bypass the per-user allowlist entirely by routing through the public router.

---

### Finding Description

**Index/identity mismatch analog**: The external bug used loop counter `i` (the position in `vaultIdx`) instead of `vaultIdx[i]` (the actual index into `freeTokenIds`), causing the wrong element to be operated on. The native analog here is that `SwapAllowlistExtension` checks `sender` — which is the **immediate caller of the pool** — instead of the **actual end user**, causing the wrong identity to be gated.

**Trace through the call stack:**

In `MetricOmmPool.swap()`, `msg.sender` is forwarded verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` passes this value straight through to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls the pool, `msg.sender` of the pool call is the **router**:

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

The actual end user (`msg.sender` of the router call) is never forwarded to the pool or the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**The inescapable dilemma for the pool admin:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users **cannot** use the router at all — broken core swap flow |
| Allowlist the router | **Every** user can bypass the per-user allowlist via the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**Direct loss / broken invariant**: A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified traders, institutional counterparties) is fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). The guard that was supposed to protect LP funds from unintended counterparties is silently skipped. LPs suffer trades against parties the pool admin explicitly intended to exclude, which can result in direct loss of LP principal through adverse selection or regulatory non-compliance.

---

### Likelihood Explanation

The router is a **public, permissionless periphery contract** that any user can call. The bypass requires no special privilege — only knowledge that the router exists. A pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address, unknowingly opening the pool to all users. Even without that admin action, the broken-functionality half of the bug (allowlisted users cannot use the router) is always present.

---

### Recommendation

Pass the originating end-user address through `extensionData` and have the extension decode it, or add a dedicated `originalSender` field to the hook signature. Alternatively, document explicitly that the allowlist gates the **immediate pool caller** (not the end user) and that the router must never be allowlisted on a per-user-restricted pool. A stricter fix is to have `MetricOmmSimpleRouter` forward `msg.sender` in `extensionData` so extensions can recover the true originator.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, user1, true)` — only `user1` is intended to swap.
3. Admin calls `setAllowedToSwap(pool, address(router), true)` — to let `user1` use the router.
4. `user2` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` of the pool call is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `user2`'s swap executes successfully despite never being allowlisted.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
