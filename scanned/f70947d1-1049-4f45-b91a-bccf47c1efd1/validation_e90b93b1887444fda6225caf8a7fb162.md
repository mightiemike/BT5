### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool, so the extension checks the router's address rather than the actual user's address. Any pool admin who allowlists the router (required for legitimate users to use the router) simultaneously opens the gate to every user on the network.

---

### Finding Description

**Pool passes its own `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap`, the `sender` forwarded to every extension hook is `msg.sender` of the pool call — the direct caller:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct pool caller
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

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

**`SwapAllowlistExtension` checks that forwarded `sender`.**

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

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**`MetricOmmSimpleRouter` is the direct pool caller.**

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

The router calls `pool.swap(...)` directly. The pool's `msg.sender` is the router, so `sender` delivered to the extension is the router's address — not the end user's address.

**The dilemma that creates the bypass.**

For a legitimate allowlisted user (`alice`) to swap through the router, the pool admin must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router, regardless of who the actual end user is. Non-allowlisted user `bob` can call `router.exactInputSingle(pool, ...)` and the extension will approve the swap because it sees `sender = router`.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled addresses). The bypass allows any unprivileged address to trade in such a pool by routing through `MetricOmmSimpleRouter`. Depending on the oracle price relative to fair value, an unauthorized trader can extract value from LP positions at prices the pool admin never intended to offer to the general public. The allowlist protection is rendered completely ineffective for any pool that also supports router-mediated swaps.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` against any pool. The pool admin has no mechanism to prevent this without also blocking legitimate allowlisted users from using the router. The bypass requires zero privileged access and is reachable in a single transaction.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary. Two viable approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a trusted convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed or verified user identity for router calls**: The extension can detect whether `sender` is a known router and, if so, require the actual user address to be supplied and verified in `extensionData`.

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is any address other than an EOA or a known, trusted contract that forwards the real user identity.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  pool admin calls: setAllowedToSwap(pool, alice, true)
  pool admin calls: setAllowedToSwap(pool, router, true)   ← required for alice to use the router

Attack:
  bob (not in allowlist) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  router calls: pool.swap(bob, true, X, ...)
    → pool sets sender = router (msg.sender)
    → pool calls extension.beforeSwap(router, bob, ...)
    → extension checks: allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, who is not in the allowlist, successfully swaps in a curated pool.
  The allowlist invariant is broken.
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
