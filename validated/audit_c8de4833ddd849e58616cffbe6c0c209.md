Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is that `msg.sender`, so the extension checks the router's allowlist entry rather than the user's. Any pool admin who allowlists the router to enable router-mediated swaps on a curated pool inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`.**

`MetricOmmPool.swap` hardcodes `msg.sender` as the `sender` argument: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` uses `sender` as the identity to gate.**

The check `allowedSwapper[msg.sender][sender]` uses `msg.sender` = pool and `sender` = whoever called `pool.swap()`: [3](#0-2) 

**Step 4 — The router calls `pool.swap()` directly with no mechanism to forward the original user's address.**

`exactInputSingle` calls `pool.swap()` making the router `msg.sender` in the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) [6](#0-5) [7](#0-6) 

**Identity mismatch table:**

| Entrypoint | `sender` seen by extension | Intended gate |
|---|---|---|
| `pool.swap()` direct | actual user | correct |
| `router.exactInputSingle()` | router address | wrong |
| `router.exactInput()` (all hops) | router address | wrong |
| `router.exactOutputSingle()` | router address | wrong |
| `router.exactOutput()` (all hops) | router address | wrong |

**Bypass path:** A pool admin who wants to support router-based swaps on a curated pool must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` returns `true` for every caller, so any unprivileged user can call `router.exactInputSingle(pool, ...)` and the extension passes unconditionally. [8](#0-7) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool receives input tokens at the oracle-derived price — a direct, repeatable economic interaction that the pool admin explicitly intended to block. This is a **High** admin-boundary break: the pool's core access-control invariant is broken for every router-mediated swap.

## Likelihood Explanation
The router is the standard periphery entrypoint for end-users. Any pool admin who deploys a curated pool and also wants to support the router (the expected production configuration) will allowlist the router, triggering the bypass. No privileged setup beyond the normal pool configuration is required; the attacker only needs to call a public router function. Likelihood is **Medium-High**.

## Recommendation
The extension must gate the economically relevant actor — the end-user — not the intermediary. Two sound approaches:

1. **Pass the original user through the router.** Add a `swapper` field to the router's swap parameters and forward it inside `extensionData`. The extension decodes `extensionData` to recover the original user and verifies it matches the router's stored payer (validated via transient storage, analogous to the existing `_setNextCallbackContext` pattern). [9](#0-8) 

2. **Gate `recipient` instead of `sender`.** If the pool's design guarantees that the recipient is always the economic beneficiary, the extension can check `allowedSwapper[pool][recipient]`. This is simpler but only correct when recipient is not an intermediate contract.

The cleanest fix consistent with the existing architecture is option 1: the router stores the original `msg.sender` in transient storage (already done for the payer in `_setNextCallbackContext`) and encodes it into `extensionData` so the extension can verify the true initiator.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router path
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // allowlist alice
  - Bob is NOT allowlisted

Attack:
  1. Bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(router, recipient, ...)
  4. Extension checks allowedSwapper[pool][router] == true  → passes
  5. Swap executes; Bob receives output tokens

Expected: revert NotAllowedToSwap (Bob is not allowlisted)
Actual:   swap succeeds — allowlist bypassed
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
