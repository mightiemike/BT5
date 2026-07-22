### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted — not the actual end user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user the ability to bypass the per-user allowlist entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument, which the pool sets to its own `msg.sender` (the direct caller of `pool.swap()`):

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no forwarding of the original caller:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

From the pool's perspective, `msg.sender = router`. The pool passes this as `sender` to `_beforeSwap`, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

The same pattern applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. In the multi-hop `exactOutput` path, intermediate hops call `pool.swap(msg.sender, ...)` where `msg.sender` is still the router:

```solidity
// MetricOmmSimpleRouter.sol line 220-228
(int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
    .swap(
        msg.sender,
        zeroForOne,
        ...
    );
```

The result is a binary choice for the pool admin:
- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken UX).
- **Allowlist the router** → every user, including those explicitly excluded from the allowlist, can swap freely through the router.

There is no configuration that allows "only allowlisted users may use the router." The allowlist invariant is structurally unenforceable through the supported periphery path.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swap access (e.g., KYC-gated, institutional-only, or whitelist-only pools) loses its access control entirely once the router is allowlisted. Any user — including those explicitly denied — can call `router.exactInputSingle()` and execute swaps against the restricted pool. This constitutes a direct bypass of the pool's core access-control policy, enabling unauthorized value extraction from a pool whose liquidity was deposited under the assumption that only vetted counterparties could trade.

### Likelihood Explanation

The trigger is a non-malicious, operationally expected action: a pool admin allowlisting the router so that their allowlisted users can benefit from the router's slippage protection, multi-hop routing, and deadline enforcement. The bypass requires no special privilege, no flash loan, and no unusual token behavior — any user with a standard ERC-20 balance can exploit it in a single transaction.

### Recommendation

The extension must verify the **original user**, not the intermediary. Two sound approaches:

1. **Pass the original payer through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of `sender` for router flows, or add a dedicated router-aware allowlist path**: The extension could accept a signed proof or a trusted forwarder pattern that preserves the original caller identity.

The simplest correct fix is to have the router encode the original `msg.sender` in `extensionData` and have the extension decode it when `sender` is a known router, falling back to `sender` otherwise.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully against the restricted pool, bypassing the per-user allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
