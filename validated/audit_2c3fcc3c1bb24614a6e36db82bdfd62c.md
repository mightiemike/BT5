Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks intermediary router address instead of actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, not the end user. If the pool admin allowlists the router (the natural action to let allowlisted users reach the pool through the standard periphery), every user — including those explicitly excluded — can bypass the guard by routing through the same public router contract.

## Finding Description

**Root cause — extension checks intermediary, not actor:**

`SwapAllowlistExtension.beforeSwap` evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

**Pool passes `msg.sender` of `pool.swap()` as `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, and `ExtensionCalling._beforeSwap` forwards that value directly as the `sender` argument to `IMetricOmmExtensions.beforeSwap`.

**Router makes itself `msg.sender` of `pool.swap()`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. The router is therefore `msg.sender` of `pool.swap()`, and the pool passes the router address as `sender` to the extension.

**Exploit path:**
1. Pool admin calls `setAllowedToSwap(pool, Alice, true)` — Alice is allowlisted.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `restrictedPool.swap(recipient, ...)` — `msg.sender = router`.
5. Pool calls `extension.beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes against the restricted pool.

**Direct call (control):** Bob calls `restrictedPool.swap(...)` directly → `allowedSwapper[pool][Bob] == false` → `NotAllowedToSwap` revert. The guard is enforced only for direct calls.

**Why existing checks fail:** There is no mechanism in the extension or the router to propagate the originating user's address. The `extensionData` field is caller-controlled and not authenticated, so the router cannot trustlessly attest to the real user. The pool admin cannot simultaneously allowlist the router (to serve allowlisted users) and block non-allowlisted users from using the same public router.

## Impact Explanation

Any user explicitly excluded from the swap allowlist can bypass the guard by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle` targeting the restricted pool. The `NotAllowedToSwap` revert is never triggered. Unauthorized users execute swaps against a pool whose admin intended to restrict access to specific counterparties, extracting value (arbitrage, price impact) from LP positions in a pool designed to be closed to them. This is a broken core pool functionality / admin-boundary break: the allowlist invariant — that only approved addresses may swap — is violated for all router-mediated calls once the router is allowlisted.

## Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural and expected operational step for any pool that wants its allowlisted users to interact through the standard periphery (`MetricOmmSimpleRouter`). The router is a public, permissionless contract; any address can call it. The two goals (allow specific users through the router; block all others) appear compatible but are not, making this a high-likelihood misconfiguration. Once the router is allowlisted, the bypass is repeatable by any unprivileged caller at zero additional cost.

## Recommendation

The extension must gate on the actual end user, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires a coordinated convention between the router and the extension (e.g., a registry of trusted routers in the extension, with authenticated `extensionData` encoding). Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this is operationally fragile and eliminates the utility of the standard periphery for restricted pools.

## Proof of Concept

**Setup (Foundry integration test):**
```solidity
// Deploy pool with SwapAllowlistExtension in beforeSwap hook order
// Pool admin allowlists Alice and the router
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true);

// Bob is NOT allowlisted
assertFalse(extension.isAllowedToSwap(pool, bob));

// Bob calls the router — swap succeeds (bypass)
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// No revert — Bob's swap executes

// Bob calls pool directly — swap reverts (control)
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
IMetricOmmPoolActions(pool).swap(...);
```

The allowlist is enforced only for direct pool calls, not for router-mediated calls, making the guard ineffective for any pool that allowlists the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
