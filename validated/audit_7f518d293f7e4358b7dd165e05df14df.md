### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any unprivileged user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The allowlist therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. If the router is allowlisted (the only way to permit any router-mediated swap on an allowlisted pool), every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that identity against the per-pool allowlist:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` inside the extension is the pool (correct), but `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

The pool therefore sees `msg.sender == router`, and the extension checks `allowedSwapper[pool][router]`. The original user's address is never consulted.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only specific addresses (e.g., KYC'd market makers).
2. To allow those addresses to use the router, the admin must also allowlist the router address.
3. Once the router is allowlisted, *any* address — including those explicitly excluded — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes, because it only sees the router.

### Impact Explanation

A pool configured with a swap allowlist to restrict trading to specific counterparties (regulatory compliance, permissioned market-making, circuit-breaker scenarios) has that restriction completely nullified for all router-mediated swaps. Any unprivileged user can execute swaps against the pool's liquidity, potentially extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal through unauthorized swap execution — matching the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact category.

### Likelihood Explanation

The router is a public, permissionless periphery contract. Any pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router, which simultaneously opens the bypass to everyone. The trigger requires no special privileges, no malicious setup, and no non-standard tokens — only a call to a public router function.

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor, not the immediate caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `sender` only when it is not a known router, and require the router to forward the original user**: Add a router-aware path in the extension that reads the true initiator from a signed or transient-storage context.

The simplest safe fix is to document that pools using `SwapAllowlistExtension` must **not** allowlist the router, and instead require allowlisted users to call `pool.swap()` directly. This should be enforced by a factory-level check or a clear invariant in the extension's `initialize`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists address(router) so that allowlisted users can use the router
  - Admin does NOT allowlist attacker (0xDEAD)

Attack:
  1. attacker (0xDEAD) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(...) — msg.sender to pool = router
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true (admin allowlisted the router)
  5. Swap executes; attacker receives output tokens
  6. SwapAllowlistExtension never checked attacker's address
```

**Corrupted value**: `allowedSwapper[pool][sender]` is evaluated with `sender = router` instead of `sender = attacker`, returning `true` when it should return `false`, causing the guard to pass for an unauthorized actor. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
