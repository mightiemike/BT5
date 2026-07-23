### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the original user. The extension therefore checks the router's allowlist status rather than the actual swapper's, breaking the per-user curation invariant.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its immediate caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Consequently the extension never sees the original EOA; it sees the router address for every router-mediated swap.

### Impact Explanation

**Bypass path (High):** A pool admin who allowlists the router address (a natural configuration to permit standard periphery access) inadvertently grants every user on-chain the ability to swap on a curated pool. Any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool; the extension checks `allowedSwapper[pool][router] == true` and passes, regardless of whether the originating EOA is on the allowlist. The curation policy is completely voided, allowing unauthorized traders to drain liquidity at oracle-derived prices from a pool that was intended to be restricted.

**DoS path (Medium):** If the router is not allowlisted, every allowlisted user who attempts to swap through the router is blocked, because the extension checks `allowedSwapper[pool][router] == false`. The router — the protocol's own standard swap interface — becomes unusable for all curated pools, breaking core swap functionality.

### Likelihood Explanation

The router is the canonical user-facing entry point documented and deployed by the protocol. Pool admins configuring a curated pool will naturally either (a) allowlist the router to permit standard access, triggering the bypass, or (b) not allowlist it, triggering the DoS. Both outcomes are reachable by any unprivileged user with no special preconditions beyond a pool having `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER`.

### Recommendation

The extension must gate on the **original user**, not the immediate pool caller. Two sound approaches:

1. **Extension-data forwarding:** Require the router to encode the originating `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`.
2. **Dedicated sender field:** Add an `originalSender` parameter to the `IMetricOmmExtensions.beforeSwap` interface that the pool populates from a transient-storage context set by the router before calling `pool.swap()`, analogous to how the router already stores payer context for callbacks.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    (admin allowlists the router to permit standard periphery access)
  pool admin does NOT allowlist attacker EOA

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
      msg.sender to pool = address(router)
    → pool calls _beforeSwap(address(router), ...)
    → SwapAllowlistExtension.beforeSwap(address(router), ...) called
      checks allowedSwapper[pool][address(router)] == true  ✓
    → swap executes for attacker despite attacker not being allowlisted

Result:
  attacker swaps on a curated pool that was intended to restrict access,
  bypassing the per-user allowlist entirely.
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
