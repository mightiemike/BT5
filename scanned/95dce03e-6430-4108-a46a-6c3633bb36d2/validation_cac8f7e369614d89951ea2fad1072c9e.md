### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural step to enable router-based swaps for their curated pool), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap()`, that argument is always `msg.sender` of the pool call:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls the pool directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [3](#0-2) 

So when any user routes through the router, `msg.sender` to the pool is the **router address**, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

The same router-as-sender pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly gates on the `owner` parameter (second argument), which the `MetricOmmPoolLiquidityAdder` always sets to the actual position owner — so the deposit path does not share this flaw. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (the only way to let their allowlisted users trade through the standard periphery) simultaneously opens the pool to **every** address. Any non-allowlisted user calls `router.exactInputSingle()`, the extension sees `allowedSwapper[pool][router] == true`, and the swap executes. The per-user curation is completely nullified: unauthorized traders can extract value from LP positions, distort pool state, and drain spread/notional fees that were intended only for curated counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. A pool admin who wants their allowlisted users to be able to trade normally (not by calling the pool contract directly) must allowlist the router. This is the expected operational configuration, making the bypass reachable by any unpermissioned address without any special privilege or setup.

---

### Recommendation

The extension must resolve the **actual end-user** rather than the immediate caller. Two sound approaches:

1. **Decode the real swapper from `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding; the extension decodes and checks that address. This requires a coordinated change to the router and extension.

2. **Check `sender` only when it is not a known periphery contract, and fall back to a user-supplied identity in `extensionData`**: The extension reads an optional `address realSwapper` from `extensionData`; if present, it gates on that address; if absent, it gates on `sender` directly (for direct pool calls).

Either way, the invariant must be: **the address checked against the allowlist is the address that economically controls the swap**, not the intermediate contract that relays it.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router so that allowlisted users can trade normally
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// attacker is NOT individually allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

// Attacker routes through the router — extension sees router address, not attacker
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        recipient:       attacker,
        tokenIn:         token0,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        extensionData:   ""
    })
);
// ✓ swap succeeds — allowlist bypassed
```

The extension evaluates `allowedSwapper[pool][router] == true` and passes, even though `attacker` was never individually permitted.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
