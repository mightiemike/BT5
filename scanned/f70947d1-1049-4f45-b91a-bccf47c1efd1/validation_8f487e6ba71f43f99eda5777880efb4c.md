### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the end user. If the pool admin allowlists the router (required for any allowlisted user to trade via the router), every unprivileged address can bypass the curated-pool restriction by routing through `MetricOmmSimpleRouter`.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no forwarding of the original user address: [4](#0-3) 

The result is a structural dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | Blocked (unusable router) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users. The extension cannot distinguish the two cases because the router's address is the only identity visible to the hook.

### Impact Explanation

Any address can trade on a curated pool (one that uses `SwapAllowlistExtension` to restrict swaps to KYC'd, whitelisted, or otherwise vetted counterparties) by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist guard is silently bypassed; the pool settles the swap and transfers tokens normally. This constitutes a curation failure and potential direct loss of LP assets if the pool's pricing or liquidity assumptions depend on a restricted counterparty set.

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. Because the router is the standard periphery entry point, any pool that intends to support router-based trading must allowlist it, making the misconfiguration the expected operational state rather than an edge case. The attacker needs no special role — a standard `exactInputSingle` call suffices.

### Recommendation

The `SwapAllowlistExtension` must receive the true end-user identity. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to both the router and the extension.
2. **Check `sender` only for direct pool calls; reject router calls**: The extension can detect that `sender` is a known router (stored at construction) and revert, forcing direct pool interaction only. This is simpler but prevents router use entirely on curated pools.

The `DepositAllowlistExtension` is not affected by the same issue because it checks `owner` (the position beneficiary), which the `MetricOmmPoolLiquidityAdder` correctly forwards as the user-supplied value. [5](#0-4) 

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists router R: E.setAllowedToSwap(P, R, true)
  attacker A is NOT allowlisted

Attack:
  A calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → router calls P.swap(recipient, ...) with msg.sender = R
  → pool calls E.beforeSwap(sender=R, ...)
  → E checks allowedSwapper[P][R] → true → passes
  → swap executes; A receives output tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; allowlist bypassed
``` [6](#0-5) [7](#0-6)

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
