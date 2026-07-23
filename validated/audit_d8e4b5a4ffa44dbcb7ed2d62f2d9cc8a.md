Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the direct pool caller, not the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` executes a swap, it is the direct caller, so `sender = router address`. Any pool that allowlists the router to support router-mediated swaps simultaneously grants unrestricted swap access to every user on the network, completely defeating the allowlist's purpose.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) executes, it calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The test suite confirms this behavior — the `TestCaller` contract (the direct pool caller) must be allowlisted, not `users[0]` (the human behind it): [5](#0-4) 

This creates an irreconcilable dilemma: if the router is not allowlisted, allowlisted users cannot swap through the router at all. If the router is allowlisted (the natural action to support the protocol's own periphery), every address on the network can bypass the allowlist by calling the router.

## Impact Explanation
High. A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (e.g., KYC'd users, whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool at oracle-derived prices, trading against LP positions that were deposited under the assumption that only allowlisted counterparties could swap. This constitutes direct loss of LP principal and fee revenue to unauthorized counterparties.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` against any registered pool. No special role or privilege is required beyond holding the input token. The only prerequisite — that the pool admin has allowlisted the router — is a natural and expected action for any pool intending to support the protocol's own router.

## Recommendation
The pool should forward the true originating user identity to the extension rather than its own `msg.sender`. Two complementary approaches:

1. **Pool-level**: Introduce an explicit `originator` parameter to `pool.swap()` that the router populates with its own `msg.sender` (the end-user). The pool forwards this value to `_beforeSwap` instead of its own `msg.sender`. This mirrors how `_setNextCallbackContext` already records `msg.sender` for the payment callback.
2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` could read the true originator from a trusted router's transient storage (similar to the existing `_getPayer()` transient storage pattern in `MetricOmmSwapRouterBase`), or restrict usage to direct pool calls only with explicit documentation.

Until fixed, pools using `SwapAllowlistExtension` must not allowlist the router address and must document that router-mediated swaps are unsupported.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists only alice: setAllowedToSwap(pool, alice, true)
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
    (required for any router-mediated swap to succeed)

Attack:
  - charlie (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) — pool's msg.sender = router
  - Pool calls _beforeSwap(router, ...) → extension.beforeSwap(router, ...)
  - Extension checks: allowedSwapper[pool][router] → true → PASSES
  - charlie receives output tokens

Result:
  - charlie bypassed the swap allowlist entirely
  - LP funds traded against an unauthorized counterparty
  - The allowlist provides zero protection for router-mediated swaps

Foundry test plan:
  - Extend FullMetricExtensionTest: allowlist only alice and the router
  - Have charlie call router.exactInputSingle targeting the pool
  - Assert the swap succeeds (no NotAllowedToSwap revert)
  - Assert charlie received output tokens despite not being individually allowlisted
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
