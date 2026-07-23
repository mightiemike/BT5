### Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any unprivileged swapper to bypass the per-user allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user who routes through it, completely defeating the per-user allowlist gate.

### Finding Description
`ExtensionCalling._beforeSwap` forwards `msg.sender` of the pool call as the `sender` argument to the extension: [1](#0-0) 

Inside `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user swaps directly, `sender` = user — the check works as intended. When a user swaps through `MetricOmmSimpleRouter`, `sender` = router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For the router to be usable at all on an allowlisted pool, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, the condition `allowedSwapper[msg.sender][sender]` is satisfied for every swap that arrives via the router, regardless of who the real end-user is. Any address — including addresses the pool admin explicitly never allowlisted — can bypass the gate by routing through the public router. [3](#0-2) 

### Impact Explanation
The swap allowlist is the pool admin's primary mechanism for restricting who may trade against the pool's liquidity. Bypassing it allows unauthorized actors to execute swaps. In an oracle-anchored pool the per-swap LP loss is bounded by the spread, but the allowlist may exist for compliance, KYC, or toxic-flow-prevention reasons. Repeated unauthorized swaps accumulate spread-bounded losses against LP principal and violate the pool admin's intended access-control invariant. This maps to the "admin-boundary break" impact class: a pool-admin-configured guard is bypassed by an unprivileged path (any user routing through the public router).

### Likelihood Explanation
High. The `MetricOmmSimpleRouter` is the standard user-facing entry point. Any pool that (a) has `SwapAllowlistExtension` configured and (b) has allowlisted the router — the only way to support router-mediated swaps — is permanently exposed. No special privilege or timing is required; any EOA can call the router.

### Recommendation
The extension must resolve the real end-user identity rather than the immediate caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes the actual swapper in `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` or require direct-only swaps**: Gate on the `recipient` argument (second parameter of `beforeSwap`) if the pool design equates recipient with the economically relevant party, or document that allowlisted pools must not be used with the public router.

The cleanest fix is for the router to encode `msg.sender` (the real user) into `extensionData` and for `SwapAllowlistExtension` to decode and check that value when present, falling back to `sender` for direct calls.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, router, true)   // enable router
  pool admin: setAllowedToSwap(pool, alice, true)    // alice is allowed
  // bob is NOT allowlisted

Attack:
  bob calls pool.swap(...) directly
    → sender = bob
    → allowedSwapper[pool][bob] = false → REVERT ✓ (correctly blocked)

  bob calls router.exactInputSingle(pool, ...)
    → router calls pool.swap(...)
    → sender = router
    → allowedSwapper[pool][router] = true → PASS ✗ (bypass)
    → bob's swap executes against LP liquidity
``` [4](#0-3) [2](#0-1)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
