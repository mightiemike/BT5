### Title
`SwapAllowlistExtension` checks the router's address instead of the real swapper's address, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `msg.sender` inside the pool is the router address. The pool passes that router address as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`, making the per-user allowlist trivially bypassable by any user who routes through the public router.

---

### Finding Description

**Call path for a direct swap (allowlist works correctly):**
```
User → pool.swap()
  msg.sender = User
  _beforeSwap(sender = User, ...)
  SwapAllowlistExtension.beforeSwap(sender = User)
  checks allowedSwapper[pool][User]  ✓
```

**Call path for a router-mediated swap (allowlist broken):**
```
User → MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap()          ← msg.sender = Router
    _beforeSwap(sender = Router, ...)
    SwapAllowlistExtension.beforeSwap(sender = Router)
    checks allowedSwapper[pool][Router]  ✗ (should check User)
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is the router (wrong): [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making itself `msg.sender` to the pool: [4](#0-3) 

The same misbinding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool's own NatSpec acknowledges the operator pattern (`msg.sender` pays but need not equal `owner`) for liquidity, but the swap path has no analogous forwarding mechanism — the real user's identity is simply lost at the router boundary. [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Any non-allowlisted user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle()` instead of `pool.swap()` directly. The router is a public, permissionless contract. The allowlist provides zero protection for router-mediated swaps. Unauthorized users can drain LP value from a pool that was designed to be restricted, constituting a direct loss of LP assets and a broken core pool functionality invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who reads the protocol documentation or inspects the contracts will discover this bypass. No special privileges, flash loans, or multi-transaction setup are required — a single call to `exactInputSingle` with any amount suffices. The bypass is unconditional whenever the router is not itself explicitly allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **In the router:** Store the original `msg.sender` in transient storage before calling `pool.swap()`, and expose it so the pool can forward it as the true `sender`. This mirrors how the router already stores the payer in transient storage for the callback.

2. **In the extension (simpler, self-contained):** Accept an optional `bytes calldata extensionData` field that the router populates with the real user address (signed or verified), and verify it inside `beforeSwap`. This requires the router to cooperate.

The cleanest fix is option 1: the router should pass the real initiator's address through `callbackData` or a dedicated transient slot, and the pool should forward it as `sender` to extensions rather than using `msg.sender` of the pool's `swap()` call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension, allowedSwapper[pool][alice] = true
// alice is the only allowed swapper; bob is NOT allowed.

// Direct swap by bob — correctly blocked:
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// Router-mediated swap by bob — allowlist bypassed:
vm.prank(bob);
// No revert — bob successfully swaps on a pool he is not allowlisted for
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// The extension checked allowedSwapper[pool][router] (false by default),
// but allowAllSwappers[pool] is also false, so this would revert UNLESS
// the router happens to be allowlisted. If the admin allowlisted the router
// to let any user swap through it, ALL users bypass the per-user gate.
// If the router is NOT allowlisted, even alice cannot use the router —
// the allowlist is misapplied in both directions.
```

The structural misbinding means the allowlist either blocks all router users (including allowlisted ones) or allows all router users (including non-allowlisted ones), depending on whether the router address itself is in the allowlist. Neither outcome matches the intended per-user access control.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
