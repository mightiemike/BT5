### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production extension that restricts which addresses may swap against a pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the router contract, not the end user. As a result, the allowlist gates on the router's address rather than the actual swapper's address. If the router is allowlisted (the only way to enable router-mediated swaps for any user), every unpermissioned address can bypass the restriction and swap against the pool.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Consequence — two mutually exclusive failure modes:**

| Admin intent | What admin does | Actual result |
|---|---|---|
| Allow specific users only | Allowlist individual EOAs | Router is not allowlisted → every allowlisted user's router call reverts (`NotAllowedToSwap`) |
| Enable router-mediated swaps | Allowlist the router address | Every unpermissioned address can call through the router and bypass the restriction |

The second mode is the critical bypass: once the router is allowlisted, `allowedSwapper[pool][router] = true` passes for every caller regardless of their individual allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner explicitly passed as a parameter), not on `sender` (the operator/adder contract): [6](#0-5) 

This asymmetry — deposit allowlist checks the economically relevant identity (`owner`), swap allowlist checks the wrong identity (`sender` = router) — is the root cause.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a set of permissioned addresses (e.g., KYC'd counterparties, private LP pools, regulatory-gated venues) can be fully bypassed by any unpermissioned user routing through `MetricOmmSimpleRouter`. The unpermissioned user executes real swaps, receives real output tokens, and the pool's LP balances are reduced exactly as in a normal swap. LP providers suffer direct loss of principal because their deposited assets are exchanged at oracle prices with counterparties the pool was explicitly configured to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any operator of a restricted pool who allowlists the router to enable normal UX immediately opens the bypass to all callers. The attacker needs no special privilege, no flash loan, and no oracle manipulation — a single `exactInputSingle` call suffices. The trigger is reachable by any EOA the moment the router is allowlisted.

---

### Recommendation

The swap allowlist must gate on the actual end-user identity, not on the intermediate caller. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the address that receives output tokens) instead of `sender`, or add a dedicated `swapper` field to `extensionData` that the router populates with `msg.sender` and the extension verifies.

2. **In `MetricOmmSimpleRouter`**: forward the original `msg.sender` inside `extensionData` so extensions can recover the true initiator, analogous to how `_setNextCallbackContext` already stores the payer: [7](#0-6) 

The cleanest protocol-level fix is to add a `swapper` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a verified source (e.g., a signed permit or a trusted router registry), mirroring how `addLiquidity` separates `sender` (operator) from `owner` (gated identity).

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that router-mediated swaps work for permitted users.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// Direct swap reverts (router not involved, pool sees Alice):
// pool.swap(...) → beforeSwap(sender=alice) → allowedSwapper[pool][alice]=false → revert

// But Alice routes through the router:
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// router calls pool.swap(...) → beforeSwap(sender=router) → allowedSwapper[pool][router]=true → PASSES
// Alice receives token1 output; pool LP balances reduced. Allowlist fully bypassed.
``` [8](#0-7) [9](#0-8)

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
