### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed from the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. If a pool admin allowlists the router (a natural action to support router-mediated swaps for permitted users), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded from `MetricOmmPool.swap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `msg.sender` to the pool is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. [3](#0-2) 

The extension interface only exposes `sender` (direct caller of `pool.swap()`) and `recipient` (output destination). Neither is the originating user when the router is the intermediary. There is no mechanism in the extension interface to recover the original user's address. [4](#0-3) 

### Impact Explanation
A pool admin who wants to support router-mediated swaps for their permitted users will allowlist the router address via `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call originating from the router, regardless of who the actual initiating user is. Any unprivileged user can then call `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) and the allowlist guard silently passes. The curated pool's access control is completely neutralized for all router-mediated paths.

The two forced choices for the pool admin are:
- **Do not allowlist the router** → no user, including permitted ones, can use the router
- **Allowlist the router** → every user, including disallowed ones, can bypass the allowlist

There is no configuration that achieves the intended goal of "allow specific users to swap via the router."

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary supported periphery entry point for end users. A pool admin deploying a curated pool with `SwapAllowlistExtension` will naturally want to support router-mediated swaps for their permitted users and will allowlist the router. The mistake is not obvious from the extension's API or documentation. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges.

### Recommendation
The extension interface must be extended to carry the original initiating user's address, or the `SwapAllowlistExtension` must document explicitly that allowlisting the router grants access to all users and that direct-pool-call-only enforcement is the only supported model. A concrete fix is to add an `originator` field to the `beforeSwap` hook arguments (populated by the pool as `tx.origin` or via a trusted forwarding pattern), or to require that the router passes the actual user's address in `extensionData` and have the extension decode and check it.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `swapExtension.setAllowedToDeposit(pool, alice, true)` — only Alice is meant to swap.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — intending to allow Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, recipient, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully on the curated pool, bypassing the allowlist entirely. [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
