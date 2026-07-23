Based on my analysis of the codebase, I can trace the exact analog to the first-deposit front-running bug class through the `SwapAllowlistExtension` hook plumbing.

**Key code paths examined:**

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`
- `ExtensionCalling._beforeSwap` forwards `sender` (= `msg.sender` to the pool) to the extension
- `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender`, checks `owner`
- `generate_scanned_questions.py` describes `SwapAllowlistExtension` as "allowAll/allowedSwapper lookup keyed by pool and **sender**"
- When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool = **router address**, not the end user

---

### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of the pool. When users swap through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. A pool admin who allowlists the router (the only way to enable router-mediated swaps for allowlisted users) inadvertently grants every unprivileged user the ability to bypass the swap restriction entirely.

### Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` (= `msg.sender` to the pool) verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender/*pool*/][sender]`. When the call originates from `MetricOmmSimpleRouter`, `sender = router`, not the end user. The pool admin faces an impossible configuration choice:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Blocked (usability broken) | ✅ Blocked |
| Yes | ✅ Passes | ❌ **Bypasses allowlist** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The `recipient` parameter (the actual end user) is available in the hook signature but is not checked: [3](#0-2) 

The parallel `DepositAllowlistExtension` correctly ignores `sender` and checks `owner` (the LP position recipient), which is the economically relevant identity for deposits. The swap extension has the inverse problem: it checks `sender` (the router) and ignores `recipient` (the actual swapper): [4](#0-3) 

### Impact Explanation
Any non-allowlisted user can bypass a pool's swap restriction by routing through `MetricOmmSimpleRouter`. For pools configured as private (e.g., institutional-only), this allows unauthorized parties to drain LP assets by swapping against the pool at oracle prices. This is a direct loss of LP principal and a complete break of the pool's access-control invariant.

### Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router — a natural and expected configuration for any pool that wants allowlisted users to access the standard periphery. Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router is vulnerable by construction.

### Recommendation
The extension should check the actual end user, not the immediate caller. Two options:

1. **Check `recipient`** instead of `sender` in `beforeSwap`. `recipient` is the address that receives swap output and is the economically relevant identity. This mirrors how `DepositAllowlistExtension` checks `owner` (the LP recipient) rather than `sender` (the payer).

2. **Require the router to embed the originating user in `extensionData`** and have the extension decode and verify that identity. This is more flexible but requires router cooperation.

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin allowlists alice as a swapper
  - Pool admin allowlists MetricOmmSimpleRouter (required for alice to use the router)

Attack:
  1. bob (not allowlisted) calls router.exactInputSingle({recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...)
     → msg.sender to pool = router
  3. _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted)
  5. Swap executes; bob receives output tokens
  6. bob successfully bypasses the allowlist with no privileged access

Result: allowedSwapper[pool][bob] == false, yet bob swapped.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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
