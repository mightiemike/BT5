Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. Any pool admin who allowlists the router to enable standard periphery UX simultaneously opens the gate to every address on-chain, completely defeating the per-user allowlist.

## Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The guard checks the wrong actor:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the originating user: [3](#0-2) 

**The router is always the direct caller:**

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees. The actual user's address is stored only in transient callback context for payment purposes and is never forwarded to the pool as `sender`: [4](#0-3) [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:**

The deposit guard correctly ignores `sender` and checks `owner` (the actual LP owner passed as an explicit parameter), which `MetricOmmPoolLiquidityAdder` populates with the real user address: [6](#0-5) [7](#0-6) 

The swap path has no equivalent separate-originator parameter, making the swap allowlist structurally broken through the router.

## Impact Explanation

Any non-allowlisted user can trade on a curated pool intended to restrict swap access (e.g., KYC-gated, institutional-only, or compliance-restricted pools) simply by calling `MetricOmmSimpleRouter` instead of the pool directly. LP funds in those pools are exposed to counterparties the pool admin explicitly intended to exclude. This constitutes a direct bypass of a deployed access-control guard with fund-impacting consequences: unauthorized price-taking against LP positions and loss of the curation guarantee the pool was deployed to enforce. Severity: **High** — complete bypass of a deployed security guard via a supported public periphery path.

## Likelihood Explanation

The attack requires no privilege. Any EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle()`. The only precondition is that the pool admin has allowlisted the router, which is the natural and necessary setup for any pool that intends to support the standard periphery UX. The bypass is deterministic and requires no timing, oracle manipulation, or special state. It is repeatable on every swap through the router.

## Recommendation

Pass the originating user address through the swap path so the extension can gate the correct actor:

1. **Preferred:** Redesign the hook interface to include an explicit `originator` parameter (analogous to `owner` in the liquidity hooks) that the pool populates from a trusted source. The router already stores the real `msg.sender` in transient context; it can also encode it into `extensionData` so the guard can decode and check it.
2. **Alternative:** Add a `payer`/`originator` field to the swap call. The router stores the real `msg.sender` in transient context already; it can encode it into `extensionData` so the guard can decode and check it.
3. **Weak fix only:** Change `SwapAllowlistExtension.beforeSwap` to check `recipient` instead of `sender` — this is weaker because `recipient` is caller-controlled.

Until fixed, pool admins must not rely on `SwapAllowlistExtension` for access control on pools reachable through `MetricOmmSimpleRouter`.

## Proof of Concept

```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX

Attack (executed by bob, who is NOT allowlisted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       ...
     })
     → router calls pool.swap(recipient=bob, ...)
     → pool calls _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
     → swap executes successfully for bob

Result:
  bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist guard is completely bypassed.

Verification that direct call is blocked:
  5. bob calls pool.swap(...) directly
     → SwapAllowlistExtension checks allowedSwapper[pool][bob] == false  ✗
     → reverts with NotAllowedToSwap  ✓ (guard works only for direct calls)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
