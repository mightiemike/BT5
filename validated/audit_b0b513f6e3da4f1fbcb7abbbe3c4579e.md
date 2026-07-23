Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every unprivileged address the ability to bypass the allowlist by routing through the same public router. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The `beforeSwap` interface provides only `sender` and `recipient` — no separate end-user field exists. `DepositAllowlistExtension.beforeAddLiquidity` handles the analogous case correctly by ignoring `sender` (first parameter, elided) and checking `owner`, the economic actor:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap path has no analogous second "economic actor" argument — the interface design itself is the root cause.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an impossible choice: either do not allowlist the router (breaking router-mediated swap UX for all legitimate users) or allowlist the router (granting every unprivileged address a bypass path). The guard fails open on the most natural production configuration. Unauthorized traders can execute swaps on a curated pool, enabling adverse selection against LP positions and violating the pool's intended access policy — a direct loss of LP value and a broken core pool access control invariant.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool admin who deploys a curated pool and wants allowlisted users to access the standard router will allowlist the router as a routine configuration step. Once the router is allowlisted, the bypass is reachable by any unprivileged address with no special preconditions, no privileged role, and no unusual token behavior. The attack is repeatable on every swap.

## Recommendation
Mirror the `DepositAllowlistExtension` pattern by gating on the economic actor rather than the caller. For swaps, the economic actor is the end user. One approach: have `MetricOmmSimpleRouter` encode the originating user address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode it when `sender` is a known router. A simpler but UX-breaking alternative is to document that the router must never be allowlisted and require allowlisted users to call the pool directly. The correct code-level fix is to redesign the `beforeSwap` interface or router forwarding so the extension always receives the true initiating user, consistent with how `beforeAddLiquidity` already receives `owner`.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension`; `allowAllSwappers[pool] = false`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the intended curated user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use `MetricOmmSimpleRouter`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes on the curated pool with no revert.

Foundry test plan: deploy pool with `SwapAllowlistExtension`, configure as above, call `exactInputSingle` from an unprivileged address, assert no revert and that the swap settles — confirming the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
