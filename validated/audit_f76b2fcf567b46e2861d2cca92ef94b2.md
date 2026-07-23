Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's immediate `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for curated users simultaneously opens the pool to every unprivileged user who calls through the same router, making the allowlist unenforceable.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the caller of the extension), and `sender` is whatever the pool passed as the first argument to `beforeSwap`.

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` unchanged into the extension call:

```solidity
// ExtensionCalling.sol lines 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)`:

```solidity
// MetricOmmSimpleRouter.sol lines 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the router, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. There is no configuration that allows allowlisted users to use the router while blocking non-allowlisted users:

| Admin configuration | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Router **not** allowlisted | Reverts | Reverts |
| Router **allowlisted** | Works | **Bypass — anyone passes** |

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses cannot enforce that restriction when the router is used. If the admin allowlists the router (the only available mechanism to enable router-mediated swaps for curated users), every unprivileged user can bypass the allowlist by calling through `MetricOmmSimpleRouter`. Non-allowlisted users can trade in the curated pool, violating the intended access control. The corrupted invariant is: `allowedSwapper[pool][userB]` is `false`, yet `userB` completes a swap in the allowlisted pool. This constitutes a broken core pool functionality (access control bypass) and an admin-boundary break where an unprivileged path circumvents a pool admin's configured restriction.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing interface for swaps. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will predictably attempt to enable router-mediated swaps for their allowlisted users by allowlisting the router — the only available mechanism — which simultaneously opens the pool to all users. The exploit requires no special privileges: any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool.

## Recommendation
The extension must check the original user identity, not the immediate pool caller. Two approaches:

1. **Extension-data forwarding**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` unchanged to every hook via `ExtensionCalling._beforeSwap`.
2. **Separate originator parameter**: Add an originator field to the `beforeSwap` hook signature so the pool can distinguish the economic actor from the immediate caller.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router-mediated swaps
4. userB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
6. Pool calls _beforeSwap(router, ...)
7. Extension checks allowedSwapper[pool][router] → true → no revert
8. userB's swap executes successfully in the curated pool.
```

**Corrupted invariant**: `allowedSwapper[pool][userB]` is `false`, yet `userB` completes a swap in the allowlisted pool. The allowlist guard is fully bypassed for any user who routes through the public router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
