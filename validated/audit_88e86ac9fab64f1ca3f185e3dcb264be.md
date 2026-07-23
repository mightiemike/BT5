Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to work), every address on the network can bypass the per-user restriction by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,  // ← always the direct caller of pool.swap()
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that this `sender` argument is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool and subsequently by the extension:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all call `pool.swap(...)` directly, making the router the `sender` seen by the extension. [4](#0-3) 

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. There is no mechanism in the extension or the pool to recover the original caller's identity from `extensionData` or any other field. [5](#0-4) 

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties). To allow those users to trade via the standard router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, any address can call `router.exactInputSingle(...)` and the extension will see `sender = router`, pass the check, and execute the swap — regardless of whether the end user is in the allowlist. The curation boundary is completely defeated, allowing unauthorized users to trade against the pool's liquidity under conditions the LPs did not consent to. This constitutes broken core pool functionality (the allowlist extension) causing potential loss of funds and violation of the pool's intended access control invariant. [6](#0-5) 

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is a public, permissionless contract. No special role, privilege, or setup is required beyond knowing the pool address. The bypass is reachable on every curated pool whose admin has allowlisted the router — the only way to let legitimate users use the router. The attacker needs only to call `exactInputSingle` with the target pool address. [7](#0-6) 

## Recommendation
The extension must check the economic actor (the end user), not the intermediary. Two complementary approaches:

1. **Pass the original caller through the router via `extensionData`.** The router populates a `payer`/`originator` field in `extensionData` with `msg.sender` before calling the pool. The extension decodes and verifies this field — but only after also verifying that the pool's direct caller (`sender`) is a trusted router (preventing a malicious caller from spoofing the field). The trusted router set can be maintained in the extension or verified against the factory registry.

2. **Require direct pool calls for curated pools.** Remove router support from pools using `SwapAllowlistExtension` and require users to call `pool.swap(...)` directly, so `msg.sender` in the pool is always the end user.

The simplest safe fix: in `beforeSwap`, when `sender` is a known trusted router address, decode an `address originator` from `extensionData` and gate on `allowedSwapper[msg.sender][originator]` instead. [5](#0-4) 

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool,
       tokenIn: token0,
       tokenOut: token1,
       zeroForOne: true,
       amountIn: 1_000,
       amountOutMinimum: 0,
       recipient: bob,
       deadline: block.timestamp + 1,
       priceLimitX64: 0,
       extensionData: ""
   }));
   ```
5. Inside `pool.swap(...)`, `msg.sender = router`. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap executes successfully despite not being allowlisted.

A Foundry integration test can confirm this by: deploying the pool with the extension, setting up the allowlist as above, impersonating an unlisted address, calling `exactInputSingle`, and asserting the swap succeeds (no `NotAllowedToSwap` revert). [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
