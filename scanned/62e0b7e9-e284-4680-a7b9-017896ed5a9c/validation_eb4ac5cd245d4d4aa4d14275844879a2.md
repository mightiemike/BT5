### Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If a pool admin allowlists the router address (the natural action to enable router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the swap gate entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the **router** the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a structural mismatch: the extension is documented as "Gates `swap` by swapper address, per pool," but the address it actually checks is the **intermediary contract** (the router), not the economic actor (the user).

This creates an irreconcilable dilemma for any pool admin who wants to run a curated pool accessible through the official router:

| Admin action | Effect |
|---|---|
| Allowlist individual users only | Allowlisted users **cannot** swap through the router (router not allowlisted → revert) |
| Allowlist the router | **All** users bypass the allowlist via the router |
| Allowlist both users and the router | Same as above — all users bypass via router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing so.

---

### Impact Explanation

A pool admin who allowlists the router address — a natural and expected action to enable the official periphery — inadvertently opens the pool to every user. Any address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and swap against a pool that was intended to be restricted to a curated set. The allowlist provides zero protection once the router is allowlisted.

This is a direct policy bypass on curated pools: unauthorized users can trade, extract liquidity-provider value, and interact with pools that were designed to exclude them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a curated pool and wants users to interact with it through the standard periphery will naturally allowlist the router. The vulnerability is triggered by a routine, well-motivated admin action with no indication in the extension's interface or documentation that doing so collapses the allowlist.

---

### Recommendation

Pass the **originating user address** to the extension rather than the direct pool caller. Two concrete options:

1. **Router-side**: Have the router encode the actual `msg.sender` (the user) into `extensionData` and have the extension decode it. This requires a convention between router and extension.

2. **Pool-side**: Add a separate `originator` field to the swap call that the router populates with its own `msg.sender`, and pass that to extensions instead of (or in addition to) `sender`. The pool can default `originator = msg.sender` for direct calls.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` — an explicit argument that the liquidity adder correctly sets to the actual user — rather than `sender`. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    (intending to allow router-based swaps for curated users)

Attack:
  attacker = address not individually allowlisted
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for attacker with no individual allowlist check

Result:
  attacker swaps successfully against a pool intended to be restricted
  SwapAllowlistExtension provides no protection for any user going through the router
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
