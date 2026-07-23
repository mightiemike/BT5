Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual end-user. If the pool admin allowlists the router — the necessary step to enable router-mediated swaps for permitted users — every unpermissioned user can bypass the allowlist by calling through the router, executing unauthorized swaps against restricted pools and consuming LP assets at live oracle prices.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`_beforeSwap` encodes that value as the `sender` argument forwarded to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))`.

**Step 2 — `SwapAllowlistExtension` checks only `sender`.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()` — the router, not the end-user.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

When a user calls `exactInputSingle`, the router becomes `msg.sender` of `pool.swap()`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Step 4 — The bypass.**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, including users who were never individually permitted. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, institutional, or compliance-restricted pools). When the router is allowlisted — the only practical way to let permitted users trade via the standard periphery — the guard is completely neutralized. Any address can execute swaps against the pool, consuming LP assets at live oracle prices and violating the pool's intended access policy. This constitutes a direct admin-boundary break with fund-impacting consequences: unauthorized swaps consume LP assets at live oracle prices, generating unauthorized volume and draining liquidity from restricted pools.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router address, which is the natural and expected operational step for any pool that wants to support the standard periphery. The router is a public, permissionless contract. No privileged attacker capability is needed beyond calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The path is reachable by any EOA or contract with no special setup.

## Recommendation
The extension must gate the **original end-user**, not the intermediary. Two complementary approaches:

1. **Preferred fix**: `MetricOmmSimpleRouter` should forward the original caller's identity in `extensionData` (e.g., `abi.encode(msg.sender)`), and `SwapAllowlistExtension.beforeSwap` should decode and verify it when `sender` is a known router. This requires a registry of trusted routers in the extension.

2. **Alternative**: Add a separate `originator` field to the pool's swap interface that the router populates with its `msg.sender` before calling the pool, and have the extension check `originator` instead of `sender`.

3. **Immediate mitigation**: Document that allowlisting the router address defeats the allowlist and instruct pool admins to allowlist individual users only, requiring them to call `pool.swap()` directly — though this eliminates router usability for restricted pools.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router for permitted users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is a permitted user
  - attacker is NOT in allowedSwapper

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, ..., extensionData)
    → pool calls _beforeSwap(msg.sender=router, recipient, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (no revert)
    → swap executes at live oracle price
    → attacker receives output tokens, LP assets consumed

Result: attacker bypasses the allowlist and executes an unauthorized swap.
```

The `sender` checked by the extension is the router address, not `attacker`. Because the router is allowlisted, the guard passes unconditionally for all router callers. [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
