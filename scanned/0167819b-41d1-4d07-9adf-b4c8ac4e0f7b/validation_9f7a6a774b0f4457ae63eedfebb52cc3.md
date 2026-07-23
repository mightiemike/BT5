### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any caller to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` enforces its allowlist against the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to let legitimate users reach the pool through the periphery, every unprivileged address on-chain gains the same access, completely defeating the allowlist.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to every extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← always the immediate caller of pool.swap()
  recipient,
  ...
);
```

**Extension checks that value against the allowlist:** [2](#0-1) 

```solidity
function beforeSwap(address sender, address, ...)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  ...
}
```

**Router calls `pool.swap()` directly — its own address becomes `sender`:** [3](#0-2) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The router stores the real user as the *payer* in transient storage for the callback, but the pool never sees that address — it only sees `msg.sender == router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

For multi-hop `exactOutput`, intermediate hops are triggered from inside `_exactOutputIterateCallback`, where `msg.sender` is the *previous pool*, not the user: [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a restricted pool with `SwapAllowlistExtension` to gate swaps to a curated set of addresses (e.g., KYC-verified counterparties). To let those users reach the pool through the standard periphery router, the admin calls `setAllowedToSwap(pool, router, true)`. At that moment, `allowedSwapper[pool][router] = true`, and the extension's `beforeSwap` check passes for **every** caller that routes through `MetricOmmSimpleRouter`, regardless of whether they are on the allowlist. Any unprivileged address can now execute swaps against the pool, draining LP-owned token balances at oracle-quoted prices the pool was not intended to offer to the general public.

Conversely, if the admin does not allowlist the router, every individually-allowlisted user is silently blocked from using the router, breaking the primary swap path for EOAs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap entry point documented in the periphery README. Any user who reads the interface and calls `exactInputSingle` or `exactInput` on a router-allowlisted restricted pool immediately bypasses the guard. No special knowledge, privilege, or front-running is required — a single standard router call suffices.

---

### Recommendation

The extension must verify the *economic actor*, not the *immediate caller*. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and verifies it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the user; however this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware allowlist**: Extend the extension to accept a `(pool, router, user)` triple so that the router can attest the real user's identity on-chain, similar to how Uniswap v4 hooks receive `msgSender` separately from the pool caller.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that alice (a legitimate user) can swap.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT on the allowlist, but he calls the router directly.
// The extension sees sender == address(router), which IS allowlisted → passes.
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        recipient:       bob,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// bob receives token1 from the restricted pool — allowlist fully bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
