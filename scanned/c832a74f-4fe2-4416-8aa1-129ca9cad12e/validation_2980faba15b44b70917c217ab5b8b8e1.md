### Title
SwapAllowlistExtension gates the immediate pool caller (router) instead of the originating user, allowing any user to bypass the allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the original user. A pool admin who allowlists the router (required for legitimate users to use it) inadvertently allows every user—including non-allowlisted ones—to bypass the swap gate by routing through the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of `MetricOmmPool.swap()`. [1](#0-0) 

In `MetricOmmPool.swap()`, the value passed as `sender` to `_beforeSwap` is `msg.sender` of the pool's `swap()` call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. [3](#0-2) 

For legitimate users to use the router on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, any user—regardless of their own allowlist status—can bypass the gate by routing through the router. The extension's own NatSpec states it "Gates `swap` by swapper address, per pool," but the checked address is the router, not the swapper. [4](#0-3) 

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards `sender` as the first argument, so the flaw is entirely in the extension's identity check, not in the hook plumbing: [5](#0-4) 

### Impact Explanation
Any non-allowlisted user can trade on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool's swap gate is rendered ineffective for all router-mediated swaps. LPs who deployed capital under the assumption that only specific counterparties could trade against them are exposed to unauthorized trading at oracle-anchored prices. Because the pool is an oracle-anchored market maker, unauthorized traders can execute at the oracle mid price, extracting value from LP positions that were intended to be accessible only to vetted counterparties.

### Likelihood Explanation
Medium. The pool admin must allowlist the router for legitimate users to use it—this is a natural and expected configuration step for any pool that intends to support the standard periphery. The bypass is then available to any user who calls the router, requiring no special privileges, no unusual token behavior, and no malicious setup beyond the ordinary router allowlist entry.

### Recommendation
The extension should gate the original user, not the immediate caller. Options:

1. Require the router to encode the original user's address in `extensionData` and have the extension decode and verify it (requires a trusted router convention).
2. Add a dedicated `originalSender` field to the extension interface so the pool can forward a router-attested address.
3. At minimum, document clearly that allowlisting the router is equivalent to `allowAllSwappers = true`, and provide a separate mechanism for per-user gating in router contexts.

### Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` so Alice can use the router.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` of this call is the router address.
5. `ExtensionCalling._beforeSwap` passes `sender = router` to the extension.
6. Extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully, bypassing the allowlist entirely. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
