### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted — not the actual user. If the pool admin allowlists the router (the natural step to let legitimate users trade through it), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

**Pool → Extension argument binding**

In `MetricOmmPool.swap`, the `_beforeSwap` hook is called with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` encodes this verbatim and dispatches it to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, ...)
    )
);
```

**Router as the caller**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutput`) calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

So `msg.sender` inside `MetricOmmPool.swap` is the **router address**, and that is what gets forwarded to the extension as `sender`.

**Allowlist checks the wrong identity**

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

- `msg.sender` = the pool (correct key for the per-pool mapping)
- `sender` = the router address (not the actual user)

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The bypass**

For legitimate allowlisted users to trade through the router, the pool admin must add the router to `allowedSwapper[pool][router]`. Once that entry exists, **any** address — allowlisted or not — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass, because the check resolves to `allowedSwapper[pool][router] == true` regardless of who the actual caller is.

The `extensionData` bytes are ignored by `SwapAllowlistExtension`, so there is no in-band mechanism for the router to forward the real user identity.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional traders, or addresses with specific risk profiles). Once the router is allowlisted — a necessary operational step for those same curated users to trade through the standard periphery — the guard is silently nullified for the entire pool. Any unprivileged address can execute swaps at the oracle-anchored bid/ask prices the pool was designed to offer only to approved parties, draining LP value at favorable prices or violating the access policy the pool was built around.

---

### Likelihood Explanation

The trigger requires two conditions that are both expected in normal operation:

1. A pool is deployed with `SwapAllowlistExtension` configured (the extension exists precisely for this use case).
2. The pool admin adds the router to the allowlist so that approved users can trade through the standard periphery (the obvious operational step).

Once both conditions hold, any unprivileged user can exploit the bypass with a single `exactInputSingle` call. No special privileges, flash loans, or oracle manipulation are required.

---

### Recommendation

The extension must gate the **actual initiating user**, not the immediate pool caller. Two sound approaches:

1. **Require callers to embed their identity in `extensionData`** and have the router sign/forward it; the extension verifies the embedded address against the allowlist. This keeps the check on-chain and avoids `tx.origin`.

2. **Check `tx.origin` as a fallback when `sender` is a known router** — acceptable only if the router is a trusted, non-upgradeable contract and the threat model excludes contract-wallet users.

The simplest correct fix is to have the extension read the actual user address from `extensionData` when `sender` is a router, or to redesign the allowlist so that the router is never a valid allowlist entry and instead the pool is called directly by approved users.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `EXTENSION_1` and `BEFORE_SWAP_ORDER = 1`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is approved.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is added so Alice can use the periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` → pool calls `extension.beforeSwap(router, ...)` → check is `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes at the oracle price on a pool that was intended to be restricted to Alice only. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
