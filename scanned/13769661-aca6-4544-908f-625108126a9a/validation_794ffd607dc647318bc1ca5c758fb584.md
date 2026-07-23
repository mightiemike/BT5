### Title
SwapAllowlistExtension Checks Router Address Instead of User Address, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for allowlisted users), every unprivileged user can bypass the per-user allowlist entirely by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its gate check as follows: [1](#0-0) 

The first argument `sender` is whatever `msg.sender` the pool received when `swap()` was called. The pool passes `msg.sender` verbatim as `sender` to every extension: [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value into the extension call: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap()`: [4](#0-3) 

At that point `msg.sender` inside the pool is the router contract, so `sender` forwarded to the extension is the router address. The extension therefore evaluates:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][user]
```

The original user's address is stored only in transient storage for the swap callback payment; it is never surfaced to the extension.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core UX |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the per-user gate by routing through the router |

The `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position recipient, the economic actor), not `sender` (the immediate caller): [5](#0-4) 

The swap extension checks the wrong identity.

### Impact Explanation

A pool admin deploys a restricted pool (e.g., KYC-only, institutional-only, or sandwich-resistant) and allowlists specific users plus the router so those users can use the standard periphery. Any non-allowlisted address can then call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting that pool. The extension sees `allowedSwapper[pool][router] == true` and passes. The unauthorized swap executes against LP capital, collecting output tokens at oracle-derived prices. LPs bear the counterparty exposure they explicitly tried to exclude. The bypass is silent — no event distinguishes a router-mediated swap by an allowlisted user from one by an unauthorized user.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router, which is the natural and expected configuration step for any pool that wants allowlisted users to access the standard periphery UX. The bypass is then reachable by any unprivileged address with no special setup, no privileged role, and no non-standard token behavior. The attacker only needs to call a public router function.

### Recommendation

The extension must gate on the economic actor, not the immediate caller. Two sound approaches:

1. **Pass the original user through the router.** Add a user-identity field to `extensionData` that the router populates with `msg.sender` before forwarding to the pool. The extension decodes and checks that field. The pool admin allowlists users, not the router.

2. **Check `sender` only when `sender` is not a known router; otherwise decode user from `extensionData`.** The extension can maintain a registry of trusted routers and fall back to an encoded user identity for router-mediated calls.

Either approach ensures `allowedSwapper[pool][user]` is evaluated regardless of the call path.

### Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists Alice and the router so Alice can use the periphery.
allowlist.setAllowedToSwap(pool, alice, true);
allowlist.setAllowedToSwap(pool, address(router), true);  // natural step

// Charlie (not allowlisted) calls the router.
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         token0,
    recipient:       charlie,
    amountIn:        1e18,
    amountOutMinimum: 0,
    zeroForOne:      true,
    priceLimitX64:   0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// Router calls pool.swap(...) with msg.sender == router.
// Extension checks allowedSwapper[pool][router] == true  → passes.
// Charlie's swap executes. Allowlist is bypassed.
``` [1](#0-0) [6](#0-5) [7](#0-6) [3](#0-2)

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
