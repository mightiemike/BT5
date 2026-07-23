### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter against a per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so `sender` forwarded to the extension is the **router address**, not the actual user. If the pool admin allowlists the router (the only way to permit router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the access control by routing through the router.

---

### Finding Description

**Actor binding in the extension:**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (used correctly as the mapping key). `sender` is the first argument forwarded by the pool.

**How the pool populates `sender`:**

`MetricOmmPool.swap()` calls:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

`msg.sender` of the pool's `swap()` call is whoever called the pool — the **router**, not the end user.

**How `ExtensionCalling` forwards it:**

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = router address
    )
);
```

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Two broken states:**

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router; they must call the pool directly, breaking the supported periphery path |
| Router **allowlisted** (admin's only fix) | Every user — including non-allowlisted ones — can bypass the curated pool's access control by routing through the router |

The second state is the direct-loss bypass: a pool admin who wants to allow router usage for their allowlisted users must add the router to the allowlist, which silently opens the gate to all users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties loses that guarantee entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` and trade against the pool's liquidity. This is a direct policy bypass with fund-impacting consequences: LP assets in a curated pool are exposed to actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The router is the primary supported periphery path (native ETH flows, multicall, multi-hop all go through it). Any pool admin who deploys a `SwapAllowlistExtension` and wants their allowlisted users to use the standard router **must** allowlist the router address — there is no other mechanism. The bypass is therefore a near-certain consequence of normal, intended usage of the extension with the router.

---

### Recommendation

The extension must identify the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool must not allow callers to forge this field without authentication (e.g., only trust it when `sender` is a known router).

2. **Check `sender` only when it is not a trusted router; otherwise decode from `extensionData`**: The extension maintains a registry of trusted routers and falls back to `extensionData`-encoded user identity for those callers.

The `DepositAllowlistExtension` avoids this problem because it checks `owner` (a caller-supplied parameter), not `sender`/`msg.sender` — the same pattern should be adopted for swap gating.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes against the curated pool's liquidity despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
