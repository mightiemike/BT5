### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-based swaps, every user — including those not individually allowlisted — can bypass the curated-pool restriction. This is a wrong-actor binding that makes per-user swap allowlisting impossible through the supported periphery path.

---

### Finding Description

**How the pool passes `sender` to the extension:**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards this value as the `sender` argument to every configured extension: [2](#0-1) 

**What `SwapAllowlistExtension` checks:**

`beforeSwap` receives `sender` as its first parameter and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender` = router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Contrast with `DepositAllowlistExtension`:**

The deposit extension correctly ignores `sender` (the LiquidityAdder contract) and checks `owner` — the actual position owner passed explicitly by the LiquidityAdder: [4](#0-3) 

The pool passes `owner` as the second argument to `_beforeAddLiquidity`: [5](#0-4) 

The `MetricOmmPoolLiquidityAdder` always passes the actual user as `positionOwner` to `pool.addLiquidity`: [6](#0-5) 

So the deposit allowlist correctly enforces per-user policy through the periphery; the swap allowlist does not.

**The asymmetry in the interface:**

`beforeSwap` has no dedicated "owner" slot — only `sender` (direct caller) and `recipient` (output destination). There is no field carrying the end user's identity when the router intermediates: [7](#0-6) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties) faces two broken states when the router is involved:

1. **Allowlist bypass (critical path):** The admin allowlists the `MetricOmmSimpleRouter` to enable router-based swaps. Because the extension sees only the router address, every user — including those not individually allowlisted — passes the check. The curated restriction is completely nullified.

2. **Allowlist over-block:** The admin does not allowlist the router. Individually allowlisted users cannot swap through the router even though they are permitted; they must call the pool directly, breaking the expected UX.

In scenario 1, non-allowlisted users can trade against a pool that was intended to be private, extracting value from LP positions that were priced for a restricted counterparty set.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract explicitly designed for curated pools.
- `MetricOmmSimpleRouter` is the canonical swap entry point for EOA users.
- A pool admin enabling router-based swaps on a curated pool will naturally allowlist the router address — the exact action that triggers the bypass.
- No privileged attacker is required; any EOA can call the router.

---

### Recommendation

The `beforeSwap` hook should gate on the actual end user, not the intermediary. Two options:

1. **Preferred — check `recipient` as a proxy for the swapper identity** (only valid if the protocol guarantees `recipient == user`; verify this invariant holds for the router).

2. **Robust — decode user identity from `extensionData`:** Require the router to embed the actual user address in `extensionData`, and have `SwapAllowlistExtension` decode and check it. This mirrors how `addLiquidity` carries `owner` explicitly.

3. **Minimum viable fix — document the limitation and add a router-aware overload:** Warn that `SwapAllowlistExtension` cannot enforce per-user policy through the router, and provide a separate extension that decodes user identity from `extensionData`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is individually allowed
  allowedSwapper[pool][bob]   = false         // bob is NOT allowed
  allowedSwapper[pool][router] = true         // admin enables router swaps

Attack:
  bob calls MetricOmmSimpleRouter.swap(pool, ...)
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check passes
    → bob's swap executes against the curated pool
    → bob bypasses the individual allowlist entirely
```

The extension's `allowedSwapper` mapping is keyed by `[pool][sender]`: [8](#0-7) 

and the check at line 37 evaluates `allowedSwapper[msg.sender][sender]` where `sender` is the router, not bob: [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
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
