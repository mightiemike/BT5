Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual end-user. If the pool admin allowlists the router to permit router-mediated swaps for legitimate users, every unprivileged user on the network can bypass the allowlist by calling the router directly.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `pool.msg.sender = router`: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same structural problem exists for multi-hop `exactInput`, where intermediate hops set the payer to `address(this)` (the router itself): [5](#0-4) 

The pool admin is left with an impossible choice: not allowlisting the router blocks all router-mediated swaps for legitimate users, while allowlisting the router opens the pool to every user on the network. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation
This is an admin-boundary break: the swap allowlist is the only on-chain mechanism a pool admin has to restrict who may trade. Any unprivileged user can bypass it by routing through the public `MetricOmmSimpleRouter`. Pools configured for permissioned access (KYC-gated, market-maker-only, or regulatory-compliance pools) are rendered fully open to any caller, breaking the admin-configured access control invariant.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who wants to trade in a restricted pool needs only to call `exactInputSingle` or `exactInput` through the router. No special privileges, flash loans, or multi-step setup are required. The bypass is trivially reachable on every allowlisted pool that also needs to support router-mediated swaps.

## Recommendation
The extension framework must propagate the original end-user address alongside the intermediary `sender`. Two viable approaches:

1. **Dedicated `originSender` field**: Add an `originSender` parameter to `beforeSwap` (and the interface) that the pool populates from a transient-storage slot set by the router at entry, similar to how the router already stores the payer in `_setNextCallbackContext`. The extension then gates on `originSender`.

2. **Extension-data convention**: Require the router to prepend the real user address to `extensionData` for allowlist-aware pools, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.

Either fix must be applied consistently to `exactInput` intermediate hops and `exactOutput` recursive callbacks, where the effective sender is `address(this)` (the router).

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists only address ALICE on pool P
  admin also allowlists router R on pool P (required for ALICE to use the router)

Attack:
  BOB (not allowlisted) calls:
    router.exactInputSingle({pool: P, recipient: BOB, ...})

Execution:
  router calls P.swap(recipient=BOB, ...)
  P.msg.sender = router R
  P calls E.beforeSwap(sender=R, ...)
  E checks: allowedSwapper[P][R] == true  ← passes (admin had to allowlist R)
  BOB's swap executes successfully

Result:
  BOB, who is not on the allowlist, completes a swap in a pool
  that was intended to be restricted to ALICE only.
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
