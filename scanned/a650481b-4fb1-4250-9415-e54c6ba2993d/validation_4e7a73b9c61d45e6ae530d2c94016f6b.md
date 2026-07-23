### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed to `beforeSwap`, which is `msg.sender` from the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks whether the **router** is allowlisted rather than whether the **actual user** is allowlisted. A pool admin who allowlists the router to support router-mediated swaps for their allowlisted users inadvertently opens the gate to all users.

---

### Finding Description

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is hardcoded as `msg.sender` — the direct caller of `pool.swap()`: [1](#0-0) 

This value is then forwarded verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses this `sender` argument as the identity to gate: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter`, the router calls `pool.swap()`, making the router `msg.sender`. The extension therefore evaluates `allowedSwapper[pool][routerAddress]`, not `allowedSwapper[pool][userAddress]`.

This creates an irresolvable design conflict for pool admins:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all — every router-mediated swap reverts.
- **If the router IS allowlisted**: any user, allowlisted or not, can bypass the gate by routing through the router.

There is no configuration that simultaneously enforces the allowlist and supports router-mediated swaps.

Note the contrast with `DepositAllowlistExtension`, which correctly checks `owner` (the LP position recipient, an explicit parameter the caller supplies) rather than `sender`: [4](#0-3) 

The swap path has no equivalent explicit-user parameter; `sender` is always the direct caller.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter` whenever the pool admin has allowlisted the router. This is a direct curation failure: the pool's intended access policy (e.g., KYC-only, institutional-only, whitelist-only) is silently voided for all router-mediated swaps. Because the pool still executes at oracle prices, the LP bears the full economic exposure of trades from actors the pool was designed to exclude.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to have allowlisted the router — a natural operational decision for any pool that intends to support the standard periphery path. The extension provides no documentation warning that allowlisting the router negates the allowlist for all users. The `SwapAllowlistExtension` interface (`setAllowedToSwap`, `isAllowedToSwap`) gives no indication that router-mediated swaps are treated differently from direct swaps.

---

### Recommendation

The `SwapAllowlistExtension` must not rely on `sender` (the direct caller of `pool.swap()`) as the identity to gate when a router intermediary is in the path. Options:

1. **Pass the actual user in `extensionData`**: require the router to encode the originating user's address in `extensionData`, and have the extension verify and decode it. The extension must also verify the caller is a trusted router to prevent spoofing.
2. **Gate on `recipient` instead of `sender`**: if the router always sets `recipient` to the actual user, checking `recipient` (the second argument to `beforeSwap`) would correctly identify the economic actor. This must be documented as a convention.
3. **Document the limitation explicitly**: if neither fix is applied, the extension must clearly state that allowlisting any intermediary contract (router, multicall, etc.) opens the gate to all users of that intermediary.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to let `alice` use the router.
4. Non-allowlisted `bob` calls `router.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(recipient=bob, zeroForOne=..., ...)`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates: `allowedSwapper[pool][router]` → `true`.
8. Swap executes successfully for `bob`, bypassing the allowlist entirely.

`bob` receives output tokens; the pool has traded with an actor the admin explicitly did not allowlist. [5](#0-4) [6](#0-5)

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
