### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any unprivileged user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the actual end-user. If the router is allowlisted for a pool (the only way to permit router-mediated swaps), every unprivileged user can bypass the allowlist by routing through the public router contract.

### Finding Description

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol ~L230
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol ~L160
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes:

```
allowedSwapper[pool][router]
```

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every user on the internet can bypass the allowlist |
| Do not allowlist the router | Individually allowlisted users cannot use the router at all |

Neither option achieves the intended goal of permitting specific users to trade through the router while blocking others. The allowlist invariant is structurally broken for any router-mediated swap.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the real caller's identity to the extension:

```solidity
// MetricOmmSimpleRouter.sol ~L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-controlled; cannot be trusted for identity
    );
```

Even if the real user's address were encoded in `extensionData`, the extension would have to trust user-supplied data, which any attacker could forge.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading (regulatory compliance, KYC gating, market-maker-only pools, or protocol-controlled liquidity) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The attacker can:

1. Execute swaps on a pool that was intended to be closed to them.
2. Drain liquidity from restricted pools at oracle-anchored prices.
3. Manipulate pool state (bin position, tick) in ways the allowlist was designed to prevent.

This is a direct loss of the access-control invariant with fund-impacting consequences for LPs and the protocol.

### Likelihood Explanation

Likelihood is **high**. The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with any pool address. No privileged setup is required beyond the pool admin having deployed the pool with `SwapAllowlistExtension` and allowlisted the router (the only way to support router-mediated swaps). The attacker needs no special role, no flash loan, and no frontrunning — a single `exactInputSingle` call suffices.

### Recommendation

The extension must gate the **real end-user**, not the intermediary. Two sound approaches:

1. **Router-level forwarding with pool verification**: Have the router encode `msg.sender` into `callbackData` or a dedicated field, and have the pool expose it as a separate `realSender` parameter to extensions. The pool (not the user) would set this field, making it unforgeable.

2. **Extension reads transient context from the router**: The router writes the real caller into transient storage before calling the pool; the extension reads it from the router's known address. This requires a trusted router registry in the extension.

Until fixed, pools relying on `SwapAllowlistExtension` for access control should not allowlist the router, accepting that allowlisted users must call the pool directly.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin sets allowedSwapper[pool][router] = true  (to support router swaps).
3. Pool admin sets allowedSwapper[pool][alice] = true   (intended allowlisted user).
4. Pool admin does NOT set allowedSwapper[pool][bob] = true (bob is blocked).

5. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
6. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData).
   → msg.sender of pool.swap() = router
7. Pool calls _beforeSwap(router, ...).
8. Extension evaluates: allowedSwapper[pool][router] == true → PASSES.
9. Bob's swap executes successfully on a pool he was supposed to be blocked from.
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
