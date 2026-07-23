### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` rather than the end user's address. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user on the network can bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = pool's msg.sender
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` — making the router the `msg.sender` to the pool. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to permit router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual end user is. The allowlist is silently voided for the entire router surface.

The `DepositAllowlistExtension` does not share this flaw — it gates on `owner` (the LP position owner), which is explicitly passed by the caller and correctly identifies the economic actor.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with a limited market-maker whitelist) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool and execute swaps as if they were allowlisted. This allows:

- Unauthorized users to extract value from LP positions in a pool that was designed to be private.
- Circumvention of any risk or compliance controls the pool admin intended to enforce via the allowlist.
- Direct loss of LP principal if the pool's pricing or liquidity geometry was calibrated for a specific, trusted set of counterparties.

---

### Likelihood Explanation

Likelihood is high. The router is the standard, documented entry point for end users. A pool admin who configures a swap allowlist and also wants to support router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. The bypass requires no special permissions, no flash loans, and no unusual token behavior — any user with knowledge of the router address can exploit it immediately after the router is allowlisted.

---

### Recommendation

Gate on the end user's identity rather than the direct caller. The simplest fix is to pass the end user's address through the router as part of `extensionData` and have the extension decode it, or to add a dedicated `swapperOverride` field to the swap parameters. Alternatively, the extension can treat the `sender` as the router and require the router to attest the real user via a signed payload in `extensionData`. A minimal on-chain fix:

```solidity
// In SwapAllowlistExtension.beforeSwap:
// If sender is a known router, decode the real swapper from extensionData
// and check allowedSwapper[pool][realSwapper] instead.
```

At minimum, the documentation must warn that allowlisting the router opens the pool to all users, and pool admins must never allowlist the router on a pool intended to restrict individual swappers.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — Alice is not allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice successfully swaps against the restricted pool despite never being allowlisted.

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
