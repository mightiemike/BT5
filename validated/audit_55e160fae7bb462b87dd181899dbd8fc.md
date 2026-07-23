Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the original EOA. This creates two mutually exclusive failure modes: either the router is allowlisted (enabling any user to bypass the curated allowlist), or it is not (locking out individually-allowlisted users from the router entirely).

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address, not the original EOA: [4](#0-3) 

The same substitution occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

So the allowlist check becomes `allowedSwapper[pool][router]` — a binary flag on the router contract — rather than `allowedSwapper[pool][alice]` for the actual user. `DepositAllowlistExtension` does not share this flaw because it checks the explicit `owner` parameter (the LP position owner), which callers pass directly: [6](#0-5) 

## Impact Explanation
Two mutually exclusive failure modes exist with no correct middle ground. **Scenario A**: If the router is allowlisted (`allowedSwapper[pool][router] = true`), any unprivileged user calls `router.exactInputSingle(pool, ...)`. The pool sees `msg.sender == router`, the extension passes, and the user swaps on a pool that was supposed to be curated. The allowlist is completely nullified for every user who knows the router exists — disallowed parties execute swaps and receive output tokens they should never have received. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact category. **Scenario B**: If the router is not allowlisted, a legitimately allowlisted user calling `router.exactInputSingle` is blocked with `NotAllowedToSwap`, matching "Broken core pool functionality causing loss of funds or unusable swap flows."

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point. No special privilege, flash loan, or oracle manipulation is required — any EOA can call `exactInputSingle`. The bypass is deterministic and repeatable every block. Pool admins who configure a `SwapAllowlistExtension` and also want users to use the router will naturally allowlist the router, unknowingly opening the bypass to all users.

## Recommendation
The extension must gate the economically relevant actor — the original initiating user — not the intermediate contract. Two options:

1. **Pass the original initiator through `extensionData`**: Require the router to encode `msg.sender` (the original EOA) into `extensionData` and have the extension decode and verify it. This requires a trusted router check inside the extension (e.g., `onlyKnownRouter` modifier) so an attacker cannot self-report a fake initiator.

2. **Redesign the hook interface**: Add an `initiator` field to `beforeSwap` that the pool populates from a transient-storage context set by the router before calling `pool.swap`. The pool would forward the true originator rather than its own `msg.sender`.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice]  = true   // alice is individually allowed
  allowedSwapper[P][router] = false  // router is not individually allowed
  allowedSwapper[P][bob]    = false  // bob is NOT allowed

Attack (Scenario A — admin allowlists router to enable alice's router use):
  admin sets allowedSwapper[P][router] = true
  bob calls router.exactInputSingle({pool: P, ...})
  → pool.swap() called with msg.sender == router
  → extension checks allowedSwapper[P][router] == true → PASSES
  → bob receives output tokens from a curated pool he was never authorized to use

Attack (Scenario B — admin does not allowlist router):
  alice calls router.exactInputSingle({pool: P, ...})
  → pool.swap() called with msg.sender == router
  → extension checks allowedSwapper[P][router] == false → REVERTS NotAllowedToSwap
  → alice, who is individually authorized, cannot use the supported periphery path

Root cause: SwapAllowlistExtension.sol line 37: allowedSwapper[msg.sender][sender]
  where sender == router address, not the original EOA
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
