### Title
`SwapAllowlistExtension` checks router address as swapper instead of original user, making per-user allowlisting incompatible with `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. This creates an irresolvable dilemma: if the router is not allowlisted, allowlisted users cannot use the router at all (broken core functionality); if the router is allowlisted to fix that, every non-allowlisted user can bypass the curation policy by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` directly, so the pool sees `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol L103-112
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        ...
    );
```

The same applies to `exactInputSingle`, `exactOutputSingle`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, never `allowedSwapper[pool][originalUser]`.

### Impact Explanation

Two fund-impacting outcomes follow directly:

1. **Broken core swap functionality for allowlisted pools.** A pool admin allowlists specific users (e.g., KYC-verified addresses). Those users call `exactInputSingle` through the router. The extension sees the router address, which is not in the allowlist, and reverts `NotAllowedToSwap`. Allowlisted users cannot use the supported periphery path at all.

2. **Complete allowlist bypass.** To fix (1), the pool admin allowlists the router address. Now `allowedSwapper[pool][router] = true`. Any non-allowlisted user calls `exactInputSingle` through the router; the extension sees `sender = router`, passes the check, and the swap executes. The curation policy is entirely defeated. Non-allowlisted users trade on a pool designed to exclude them, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point in `metric-periphery`. Any pool that deploys `SwapAllowlistExtension` for curation will immediately encounter this mismatch the first time an allowlisted user tries to use the router. The pool admin's only apparent fix (allowlisting the router) opens the bypass. The trigger requires no special privileges: any ordinary user calling `exactInputSingle` or `exactInput` on an allowlisted pool reproduces both failure modes.

### Recommendation

The pool must receive the original initiating user's address and forward it to extensions. Two approaches:

**Option A – Pass originator through `extensionData`.** The router encodes `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`. This requires a convention between router and extension.

**Option B – Add an originator field to the swap interface.** Extend `IMetricOmmPoolActions.swap` with an explicit `originator` parameter. The pool passes it to `_beforeSwap` alongside `sender`. Extensions can then gate on the true economic actor regardless of which intermediary called the pool.

Either way, `SwapAllowlistExtension` must check the address of the user who initiated the transaction, not the address of the contract that called `pool.swap()`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Alice calls `router.exactInputSingle(...)` targeting that pool.
4. Inside `pool.swap()`, `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`. Alice cannot trade through the router despite being allowlisted.
5. Pool admin calls `setAllowedToSwap(pool, router, true)` to unblock Alice.
6. Bob (not allowlisted) calls `router.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes. Bob trades on the curated pool, bypassing the allowlist entirely.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
