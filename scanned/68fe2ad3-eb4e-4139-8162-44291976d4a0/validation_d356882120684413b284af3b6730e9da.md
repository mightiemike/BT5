### Title
`SwapAllowlistExtension.beforeSwap` checks the direct pool caller (`sender`) instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `sender`, not the end user. If the router is allowlisted (or `allowAllSwappers` is set), any non-allowlisted user can bypass the curated pool's swap restriction by routing through the router. If the router is not allowlisted, allowlisted users cannot use the router at all — making the extension fundamentally incompatible with the supported periphery path.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
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

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call.

In `MetricOmmPool.swap`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the direct caller of pool.swap
    recipient,
    ...
);
```

When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The router is `msg.sender` to the pool, so the pool passes `sender = router` to `_beforeSwap`. The extension then checks `allowedSwapper[pool][router]` — the router's address — not the end user's address.

This creates an irreconcilable dilemma for any pool admin who configures `SwapAllowlistExtension`:

- **If the router is allowlisted** (so that legitimate users can use it): any non-allowlisted user can bypass the allowlist by calling `router.exactInputSingle(pool, ...)`. The extension sees `sender = router` and passes.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the supported periphery path.

The `DepositAllowlistExtension` does not share this flaw — it correctly checks `owner` (the position owner), which is the economically relevant actor regardless of who the payer/caller is.

The `BaseMetricExtension` base class also lacks `onlyPool` in the overriding `SwapAllowlistExtension.beforeSwap`, meaning any address (not just factory-registered pools) can call the hook. While a non-pool caller would be denied by default (empty allowlist), this removes the factory-registry identity guarantee that `onlyPool` provides.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled addresses) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. If the router is allowlisted — a natural configuration for any pool that wants its users to benefit from the router's slippage protection and multi-hop routing — the allowlist is effectively open to all. Unauthorized users can execute swaps against the pool's liquidity, violating the pool admin's curation policy and potentially causing direct financial loss to LPs who deposited under the assumption that only vetted counterparties would trade.

---

### Likelihood Explanation

The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool address. No special role, no admin action, and no precondition beyond the router being allowlisted (which is the natural configuration for usability). The router is a supported, documented periphery contract. Pool admins who configure the allowlist are likely to allowlist the router to avoid locking out their own users from the periphery path.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Add `onlyPool` to `beforeSwap`** (matching the base class default) so only factory-registered pools can invoke the hook.

2. **Check the correct identity**: The pool should forward the original end-user address separately from the direct caller. One approach is to pass the end user as the `recipient` and check that, or to have the router forward the original `msg.sender` via `extensionData` and have the extension decode it. Alternatively, align with the `DepositAllowlistExtension` pattern: gate on the `recipient` (the economic beneficiary of the swap) rather than `sender` (the intermediary).

---

### Proof of Concept

```
Setup:
  - Deploy SwapAllowlistExtension
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist Alice: allowedSwapper[pool][alice] = false

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=alice, ...)  ← msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true (router is allowlisted)
  5. beforeSwap returns success selector
  6. Swap executes — Alice, a non-allowlisted user, has swapped on the curated pool

Result: The swap allowlist is bypassed. Alice receives tokens from the pool despite
        not being on the allowlist. Any non-allowlisted user can repeat this.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
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
