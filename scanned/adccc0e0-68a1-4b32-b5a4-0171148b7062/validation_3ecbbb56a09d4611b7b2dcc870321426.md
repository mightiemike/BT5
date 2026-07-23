### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, the allowlist is completely bypassed for every user on-chain.

### Finding Description

**Allowlist check identity:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the address the pool received as `msg.sender` when `swap()` was called.

**What the pool passes as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The dilemma this creates for the pool admin:**

The pool admin faces two equally broken outcomes:

1. **Do not allowlist the router** → legitimate allowlisted users cannot swap through the router at all (the extension sees the router address and reverts).
2. **Allowlist the router** → the allowlist is completely bypassed; any user can call `exactInputSingle` on the router and the extension passes because `allowedSwapper[pool][router] == true`.

There is no mechanism in the current design to thread the original end-user address through the router to the extension. The `sender` field is structurally bound to the direct caller of `pool.swap()`.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the pool admin allowlists the router (the natural action to support router-mediated swaps), the allowlist provides zero protection. Unauthorized users can execute swaps against the pool's LP positions, extracting value at oracle-anchored prices and causing direct loss of LP principal.

### Likelihood Explanation

The likelihood is medium-high. The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a restricted pool and then tries to make it usable via the router will naturally add the router to the allowlist. The misconfiguration is not obvious from the extension's interface or documentation, and the unit tests for `SwapAllowlistExtension` only test direct pool calls (not router-mediated calls), so the bypass is not caught by the existing test suite. [5](#0-4) 

### Recommendation

The `beforeSwap` hook should receive and check the **original end-user address**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original payer through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks this address. This requires a convention between the router and the extension.

2. **Add an `originator` field to the swap interface**: Extend `IMetricOmmPoolActions.swap()` with an explicit `originator` parameter that the router sets to `msg.sender` (the end user). The pool passes this to `_beforeSwap` alongside `sender`. The extension checks `originator` instead of `sender`.

Option 2 is cleaner and avoids relying on `extensionData` conventions. Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should document that router-mediated swaps are incompatible with the allowlist guard.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow legitimate users to swap via the router).
  - Pool admin does NOT add attacker's address to the allowlist.

Attack:
  1. Attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=restrictedPool, tokenIn=..., amountIn=..., ...
     ).
  2. Router calls restrictedPool.swap(recipient, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  Attacker bypasses the allowlist and swaps on a restricted pool,
  extracting LP value at oracle-anchored prices without authorization.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
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
