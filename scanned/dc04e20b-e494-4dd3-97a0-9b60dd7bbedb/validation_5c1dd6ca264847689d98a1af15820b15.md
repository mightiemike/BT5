### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to work on the curated pool), every user — including non-allowlisted ones — can bypass the allowlist by calling the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender`, not the end user.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

`sender` here is whoever called `pool.swap()` — the router, not the end user.

**Step 3 — The router calls `pool.swap` as itself.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 4 — The bypass.**

For any allowlisted user (e.g., Alice) to trade via the router on a curated pool, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller — including Bob, who was never individually allowlisted. Bob calls `router.exactInputSingle(...)`, the router calls `pool.swap(...)`, the extension sees `sender = router`, the check passes, and Bob's swap executes.

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` and checks `owner` (the position owner the pool will actually credit): [5](#0-4) 

The swap extension has no equivalent "economically correct" field to fall back on — `recipient` is the output receiver, not the payer — so the mismatch is structural.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted bots) provides no real restriction once the router is allowlisted. Any unpermissioned address can trade against the pool's liquidity, violating the pool admin's curation policy and potentially draining LP value through adversarial order flow the pool was specifically designed to exclude.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for end users. A pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is the expected operational configuration, not an edge case. The bypass is therefore reachable on any production allowlisted pool that supports router-mediated swaps.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the economically relevant actor, not the immediate caller. Two viable approaches:

1. **Check `recipient` as a proxy for the beneficiary** — only valid if the pool's curation intent is to restrict who receives output, which is not always the case.
2. **Require the router to forward the original user identity in `extensionData`** and have the extension decode and verify it. The router already forwards `extensionData` unchanged to the pool, so the end user can supply their own address; the extension would verify it matches a signed or pre-registered claim.
3. **Do not allowlist the router; require allowlisted users to call `pool.swap` directly.** This is the simplest fix but removes router UX for curated pools.

The root fix is that `sender` must represent the end user, not an intermediary contract.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook
  pool admin calls setAllowedToSwap(pool, alice, true)      // Alice is the intended gated user
  pool admin calls setAllowedToSwap(pool, router, true)     // required so Alice can use the router

Attack (Bob, never allowlisted):
  bob calls router.exactInputSingle({
      pool:      pool,
      tokenIn:   token0,
      zeroForOne: true,
      amountIn:  X,
      recipient: bob,
      ...
  })

  router calls pool.swap(bob, true, X, ..., extensionData)
  pool calls _beforeSwap(msg.sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  swap executes; Bob receives output tokens

Result: Bob bypasses the allowlist entirely.
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
