### Title
`SwapAllowlistExtension` Bypass via Router — Any User Can Swap in Allowlisted Pools by Routing Through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the actual end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for legitimate users), every unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool's `msg.sender` is the router.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to `SwapAllowlistExtension`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The check never touches the actual end user's address.

**Relevant code:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router: [3](#0-2) 

The same identity mismatch applies to all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), because every one of them calls `pool.swap(...)` directly from the router contract: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, internal market makers). To allow those users to trade via the public router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any address** — including completely unprivileged users — can call any router entry point and execute swaps against the restricted pool. The per-user allowlist is entirely inoperative for router-mediated paths.

Consequence: unauthorized users can consume LP liquidity in a pool that was designed to be access-controlled, directly draining LP principal and owed fees from the pool.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract explicitly designed for per-pool access control.
- Any operator who deploys a restricted pool and also wants to support the standard router (the primary user-facing entry point) will inevitably allowlist the router, triggering the bypass.
- No special privilege, flash loan, or unusual token behavior is required — a plain `exactInputSingle` call suffices.
- The bypass is reachable by any EOA or contract with no preconditions beyond having tokens to swap.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **economically relevant actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the router's swap parameters and forward it as `extensionData`; have the extension decode and check it. This requires a coordinated change in the router and extension.

2. **Check `recipient` instead of (or in addition to) `sender`.** For single-hop swaps the recipient is the intended beneficiary; however, for multi-hop swaps the recipient of intermediate hops is the router itself, so this alone is insufficient.

3. **Simplest safe fix:** Change `SwapAllowlistExtension` to check `allowedSwapper[pool][tx.origin]` when `sender` is a known router, or require callers to supply a signed proof of identity in `extensionData` that the extension verifies.

The deposit extension correctly avoids this problem by checking `owner` (an explicit parameter the pool records for position accounting) rather than `sender`: [6](#0-5) 

The swap extension should adopt an equivalent design — gate on the identity the pool will actually attribute the trade to, not the intermediary that forwarded the call.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order = extension 1)
  allowedSwapper[pool][alice] = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true  // admin must set this for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
    })

  Execution:
    router → pool.swap(bob, true, X, ...)
      pool: msg.sender = router
      pool calls _beforeSwap(sender=router, ...)
      SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
      swap executes, bob receives output tokens

Result:
  bob swapped successfully despite never being allowlisted.
  The allowlist guard is completely bypassed.
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
