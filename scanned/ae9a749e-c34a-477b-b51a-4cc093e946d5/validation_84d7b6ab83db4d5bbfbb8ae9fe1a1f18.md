### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, making the per-pool swap allowlist bypassable via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap()` passes `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is the `msg.sender` when users route through it, the allowlist check resolves to the router's address rather than the actual end-user's address. A pool admin who allowlists the router to let legitimate users access the pool via the standard periphery path simultaneously opens the gate to every non-allowlisted user.

---

### Finding Description

**Call chain:**

```
User (Eve, not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
          // msg.sender in pool = router address
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  // checks allowedSwapper[pool][router]  ← router address, NOT Eve
```

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router when users go through periphery: [3](#0-2) 

The router calls the pool with no forwarding of the original `msg.sender`: [4](#0-3) 

**The dilemma this creates for the pool admin:**

| Router allowlisted? | Legitimate user via router | Non-allowlisted user via router |
|---|---|---|
| No | **Blocked** (UX broken) | Blocked |
| Yes | Passes | **Also passes** (bypass) |

There is no configuration that simultaneously allows legitimate allowlisted users to use the router and blocks non-allowlisted users from doing the same. The pool admin is forced to either break the standard UX path or render the allowlist ineffective for all router-mediated swaps.

The existing test suite confirms the allowlist is only tested with direct pool callers (`TestCaller` instances), never with the router: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd users, institutional counterparties) can be bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The extension's entire purpose — gating the swap action to a curated set of addresses — is nullified for the primary periphery entry point. Unauthorized users can execute swaps on pools that should be closed to them, breaking the pool admin's curation policy and any compliance or access-control guarantees the pool was designed to enforce.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. Any non-allowlisted user who discovers the allowlist blocks their direct pool call can trivially route through the router instead. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

Pass the original end-user identity through the swap path so the extension can check it. Two concrete options:

1. **Extend the pool's `swap` signature** to accept an explicit `swapper` address (similar to how `addLiquidity` separates `msg.sender` from `owner`), and have the router forward `msg.sender` in that field. The extension then checks the explicit `swapper` rather than the pool's `msg.sender`.

2. **Check `recipient` instead of `sender`** if the pool's design guarantees that the recipient is always the economic beneficiary. This is a weaker fix because `recipient` can also be set to an arbitrary address.

The cleanest fix is option 1: add a `swapper` parameter to `swap()` and have `MetricOmmSimpleRouter` populate it with `msg.sender`, mirroring the `owner`/`sender` separation already present on the `addLiquidity` path.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin allowlists alice (EOA) and the router address
//    (required so alice can use the router).
// 3. Eve (not allowlisted as an EOA) calls via the router.

function test_swapAllowlist_bypassViaRouter() public {
    // Pool admin allowlists alice AND the router so alice can use periphery
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Add liquidity so there is something to swap
    _addLiquidity(...);

    // Eve is NOT individually allowlisted
    // Eve routes through the public router — sender seen by extension = router address
    vm.prank(eve);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: eve,
            tokenIn: address(token0),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // Eve's swap succeeds — allowlist bypassed
}
```

The check `allowedSwapper[pool][router] == true` passes for Eve because the extension sees the router address, not Eve's address. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-84)
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

```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
