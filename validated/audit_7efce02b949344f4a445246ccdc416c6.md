### Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (required for any allowlisted user to trade via the router), every user on the network can bypass the allowlist by routing through the same public contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension:

```solidity
// MetricOmmPool.sol L231
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, not the originating EOA
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originating_user]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific EOAs (e.g., KYC-verified addresses).
2. An allowlisted user needs to trade via the router (e.g., for multi-hop or slippage protection), so the admin must also add `allowedSwapper[pool][router] = true`.
3. Once the router is allowlisted, any non-allowlisted user calls `router.exactInputSingle(pool, ...)`. The pool sees `msg.sender = router`, the extension checks `allowedSwapper[pool][router] = true`, and the swap executes without restriction.
4. The non-allowlisted user receives pool output tokens, draining LP assets or violating the curation policy.

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`. This directly violates the allowlist invariant: LP assets in a restricted pool are exposed to unrestricted trading. Depending on pool design, this can cause direct loss of LP principal (e.g., a pool configured to trade only at favorable oracle prices for a specific counterparty set), or break compliance requirements that the allowlist was meant to enforce.

### Likelihood Explanation

The trigger is fully unprivileged: any EOA calls a public periphery function. The precondition (router being allowlisted) is a necessary operational step for any allowlisted user who wants to use the router, making it a realistic production configuration. No special timing or oracle manipulation is required.

### Recommendation

The `SwapAllowlistExtension` must gate on the originating user, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** if the pool's design intent is to gate who receives output (but this changes semantics).
2. **Require the router to forward the originating user** via `extensionData`, and have the extension decode and verify that identity — but this requires router cooperation and is fragile.
3. **Preferred:** Gate on `sender` but require that any allowlisted intermediary (router) is not a public contract — i.e., do not allowlist the router; instead, require allowlisted users to call the pool directly. Document this constraint explicitly.
4. **Structural fix:** Pass the originating caller through the extension data pipeline so the allowlist can check the true economic actor regardless of routing path.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice and the router (so alice can use the router).
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attacker (not allowlisted) routes through the public router.
// The pool sees msg.sender = router → allowedSwapper[pool][router] = true → passes.
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Attacker receives token1 output despite not being on the allowlist.
assertGt(token1.balanceOf(attacker), 0);
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
