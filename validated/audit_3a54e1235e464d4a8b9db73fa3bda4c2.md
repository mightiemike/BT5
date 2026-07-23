### Title
`SwapAllowlistExtension` checks the router address instead of the real user, allowing any unprivileged caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender = router`. If the pool admin allowlists the router (the only way to let legitimate users trade through the standard periphery), every unprivileged address can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The result is a forced choice for the pool admin:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (unusable periphery) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same, because the extension sees only the router address, not the real end-user.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` argument (the position beneficiary), which the adder passes through unchanged regardless of who the payer is. [5](#0-4) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is designed so that only trusted counterparties trade against LP positions. Once the pool admin allowlists the router (the only way to let legitimate users access the standard periphery), any address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool. Non-allowlisted actors can extract value from LPs through adverse selection, constituting a direct loss of LP principal. This is a broken core pool functionality: the allowlist guard fails open for the entire public user base whenever the router is enabled.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user who can call the public router can exploit this. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool that wants its allowlisted users to access the standard periphery. The bypass is reachable on every swap entry point of the router (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

The extension must verify the real end-user, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** The router already stores the real payer in transient storage (`_getPayer()`). Add a `realSender` field to the extension data or a dedicated transient slot that the pool reads and forwards to extensions as the authoritative swapper identity.

2. **Check `recipient` instead of `sender` when the sender is a known periphery contract.** This is fragile; approach 1 is preferred.

3. **Require direct pool calls for allowlisted pools.** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting router-allowlisted configurations.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
//   extension = SwapAllowlistExtension
//   allowedSwapper[pool][alice] = true        // alice is the only intended trader
//   allowedSwapper[pool][router] = true       // admin must add this so alice can use the router

// Attacker (bob, not allowlisted) bypasses the guard:
IMetricOmmSimpleRouter.ExactInputSingleParams memory p = IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(curatedPool),
    recipient:       bob,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    tokenIn:         token0,
    deadline:        block.timestamp,
    extensionData:   ""
});

// bob calls the public router — sender seen by the extension is router, which IS allowlisted
router.exactInputSingle(p);
// swap succeeds; bob trades in a pool he was never supposed to access
```

The extension receives `sender = router` (allowlisted) and passes the check, even though the real actor is `bob` (not allowlisted). [6](#0-5) [7](#0-6)

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
