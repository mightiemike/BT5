### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the natural operational choice to let users access the pool through the supported periphery), every unprivileged user can bypass the per-user allowlist and execute swaps on a curated pool.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
              msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._callExtensionsInOrder(...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                             checks allowedSwapper[pool][router]   ← wrong actor
```

**Root cause — `SwapAllowlistExtension.beforeSwap` (line 37):**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument, which the pool sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called through router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

So the extension receives `sender = router`, and the check becomes `allowedSwapper[pool][router]`. The actual user's address is never consulted.

**The bypass:**

A pool admin who wants to allow router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the real user is. Any address — including addresses the admin explicitly never allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap(...)` with the router as `msg.sender`.

---

### Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). The bypass allows any unprivileged user to:

- Execute swaps against the pool's liquidity at oracle-anchored prices, extracting value from LP positions that were deposited under the assumption that only trusted counterparties could trade.
- Drain one side of the pool's liquidity if the oracle price diverges from the market, since the stop-loss or velocity guard extensions are the only remaining protections and they operate on price movement, not identity.

This is a **direct loss of LP principal** on pools where the allowlist is the primary access-control mechanism.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, documented user-facing entry point for swaps.
- A pool admin who deploys a curated pool and wants to support router-mediated swaps for their allowlisted users must allowlist the router. This is the expected operational path.
- Once the router is allowlisted, the bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.
- The flaw is invisible in unit tests that call the pool directly (as the existing `FullMetricExtensionTest` does), because direct calls correctly bind the user's address as `sender`.

---

### Recommendation

The extension must recover the real initiating user rather than the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router as part of `extensionData`** and have the extension decode and verify it — but this is fragile because the router could be bypassed or the data forged.

2. **Preferred: check `sender` against the allowlist only when `sender` is not a known periphery contract; otherwise check the payer stored in the router's transient context** — but this couples the extension to the router.

3. **Cleanest: gate on `tx.origin` as a secondary check when `sender` is a contract** — acceptable for allowlist purposes where the goal is to identify the human initiator, though `tx.origin` has its own caveats.

4. **Architectural fix: the router should forward the real user address as an explicit parameter** (e.g., in `extensionData`) and the extension should decode and verify it, with the router signing or hashing the payload so it cannot be spoofed by a direct pool caller.

Short-term: document that allowlisting the router on a `SwapAllowlistExtension`-protected pool effectively opens the pool to all users, and require pool admins to allowlist individual users only (accepting that those users cannot use the router).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.
// Router IS allowlisted (pool admin called setAllowedToSwap(pool, router, true)).

// Step 1 – direct swap by attacker reverts correctly:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Step 2 – router-mediated swap by attacker succeeds (bypass):
token0.approve(address(router), type(uint256).max);
vm.prank(attacker);
// No revert — extension sees sender=router, which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        amountIn:        1000,
        amountOutMinimum: 0,
        zeroForOne:      true,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// attacker received token1 from a pool they were never supposed to access.
```

**Key references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
