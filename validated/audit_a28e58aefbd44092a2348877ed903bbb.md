### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension sees the router address — not the actual end-user. For router-mediated swaps to function on an allowlisted pool, the pool admin must allowlist the router. Once the router is allowlisted, every user on the network can bypass the per-user swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` inside `pool.swap` is the **router contract**, not the end-user. The extension therefore checks whether the **router** is allowlisted, not whether the actual user is allowlisted.

For any router-mediated swap to succeed on an allowlisted pool, the pool admin must add the router to the allowlist. The moment the router is allowlisted, the check is trivially satisfied for every user who routes through it, because the extension cannot distinguish between different callers of the router.

### Impact Explanation
**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified, institutional, or otherwise vetted addresses is fully bypassed. Any address — including those explicitly excluded from the allowlist — can execute swaps against the pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The pool receives and pays out real tokens on each such swap, so the bypass directly affects live fund flows and defeats the pool's curation invariant.

### Likelihood Explanation
**Medium.** The router is a standard, publicly deployed periphery contract. Any pool that wants to support router-mediated swaps (the normal user path) must allowlist the router. The bypass is therefore automatically available on every allowlisted pool that also supports the router — no special setup or privileged access is required by the attacker.

### Recommendation
The extension must gate the **actual end-user**, not the intermediary. Two complementary approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust that the router is honest — acceptable only if the router is a known, immutable contract.

2. **Check `sender` AND require `sender` to not be a known router**: Maintain a registry of trusted routers; when `sender` is a router, decode the real user from `extensionData` and check that address instead.

The simplest safe fix is to remove the router from the allowlist and require users to call the pool directly, but this breaks the standard UX. The correct long-term fix is to thread the originating user identity through the call stack so the extension can always check the economically relevant actor.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: allowlistedPool,
         recipient: attacker,
         ...
     })
  2. Router calls pool.swap(...) — msg.sender inside pool = router
  3. _beforeSwap(sender=router, ...) is called
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives output tokens

Result: attacker (not individually allowlisted) successfully swaps on a curated pool,
        bypassing the per-user allowlist entirely.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
