### Title
`SwapAllowlistExtension` Swap Guard Bypassed via `MetricOmmSimpleRouter` — Router Address Replaces End-User Identity in `sender` Check - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of the `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual swapper is allowlisted. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every user on the network.

---

### Finding Description

**Identity binding mismatch in the swap allowlist hook**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So `msg.sender` to the pool is `address(router)`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

**The dilemma this creates for pool admins:**

| Router allowlisted? | Outcome |
|---|---|
| No | Legitimate allowlisted users cannot use the router at all |
| Yes | Every user on the network can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` (e.g., a KYC-gated or institutional pool) is intended to restrict swaps to specific addresses. Once the pool admin allowlists the router — a necessary step for any user to swap via the standard periphery — the guard is completely neutralised. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against LP liquidity that was intended to be restricted. This constitutes a broken core pool functionality (allowlist guard bypass) with direct fund-impacting consequences: LP positions are exposed to swaps from actors the pool was explicitly configured to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` pool and want legitimate users to access it via the standard router will naturally allowlist the router. The bypass is then reachable by any address with no special privileges, no flash loan, and no unusual token behaviour — a single call to `exactInputSingle` suffices.

---

### Recommendation

The `sender` forwarded to extensions should reflect the **economic actor** (the end user), not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the original `msg.sender` (the end user) as the `recipient`-equivalent identity in a dedicated field, or the pool interface should accept an explicit `swapper` parameter distinct from the callback payer.

2. **Extension-side**: `SwapAllowlistExtension` should check the `recipient` parameter (which the router sets to the actual user) rather than `sender` when the `sender` is a known router, or the protocol should define a standard for extensions to recover the true initiator.

The cleanest fix is to add an explicit `originator` field to the swap call that the router populates with `msg.sender` and that the pool forwards to extensions, analogous to how `addLiquidity` separates `msg.sender` (payer) from `owner` (position holder).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true        // alice is the intended gated user
  allowedSwapper[pool][router] = true       // admin must do this for router to work
  bob is NOT in the allowlist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      ...
  })

  Router calls pool.swap(bob, ...) → msg.sender = router
  Pool calls _beforeSwap(router, bob, ...)
  Extension checks allowedSwapper[pool][router] → true ✓
  Swap executes — bob receives tokens from LP liquidity
  NotAllowedToSwap is never triggered
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
