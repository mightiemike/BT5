### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` is the router address, not the actual user. A pool admin who allowlists the router to permit legitimate router-mediated swaps simultaneously opens the gate to every non-allowlisted user, fully defeating the curation policy.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when routed
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then uses `sender` as the identity to gate:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct pool-key), and `sender` is the direct caller of `pool.swap()`. When a user goes through `MetricOmmSimpleRouter`, the router is that direct caller. The check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → every legitimate user who uses the router is blocked, even if they are individually allowlisted.
- **Allowlist the router** → every non-allowlisted user can bypass the curation policy by routing through the router.

The analog to the external LSP0 report is exact: in LSP0 a `bytes32` typeId is silently trimmed to `bytes20` when constructing the delegate lookup key, so the wrong delegate is resolved. Here the actual user address is silently replaced by the router address when constructing the allowlist lookup key, so the wrong actor is checked.

### Impact Explanation

Any non-allowlisted user can bypass a curated pool's swap allowlist by calling `MetricOmmSimpleRouter` instead of `MetricOmmPool.swap()` directly, provided the pool admin has allowlisted the router (which is the natural step to take when the pool is meant to be accessible via the router). The allowlist extension provides zero protection against router-mediated swaps in that configuration. This breaks the core pool functionality of access-controlled pools and can result in unauthorized swaps draining LP value in pools that were designed to be curated.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and supported by the protocol. Any pool admin who deploys a curated pool and also wants legitimate users to access it via the router will allowlist the router, unknowingly opening the bypass. The exploit requires no special privileges, no flash loans, and no complex setup — any EOA can call the router.

### Recommendation

Pass the original user's address through the call chain rather than the direct caller. One approach: add an explicit `swapper` parameter to `pool.swap()` that the router populates with the end user's address (similar to how `addLiquidity` separates `sender` from `owner`). The `SwapAllowlistExtension` should gate on that explicit swapper identity. Alternatively, document clearly that the allowlist gates the direct caller of `pool.swap()` and that the router must never be allowlisted on curated pools, and enforce this at the factory or extension initialization level.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for legitimate users.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the curated pool.
4. The router calls `pool.swap(recipient, ...)` — `msg.sender` of `pool.swap()` is the router.
5. `_beforeSwap(router, ...)` is dispatched; `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` → `true`.
6. The swap executes. The attacker, who was never individually allowlisted, has bypassed the curation gate.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

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
