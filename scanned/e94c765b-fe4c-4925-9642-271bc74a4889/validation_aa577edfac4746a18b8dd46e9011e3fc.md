### Title
SwapAllowlistExtension gates on router address instead of end-user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` of the `MetricOmmPool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the end-user's address. If the pool admin allowlists the router (a necessary step to permit router-mediated swaps for allowlisted users), the check passes for **every** user who routes through the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this verbatim to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap()` then checks:

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

Here `msg.sender` is the pool and `sender` is the caller of `pool.swap()`. When the router calls `pool.swap()`, `sender` = router address. The check becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router **must** call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual end-user is. The allowlist is silently bypassed for all router-mediated swaps.

There is no mechanism in the current design to express "allowlisted users may use the router" without simultaneously expressing "all users may use the router."

---

### Impact Explanation

Any user can swap on a curated (allowlist-restricted) pool by routing through `MetricOmmSimpleRouter` whenever the router has been allowlisted. Curated pools are typically configured with tighter spreads or specific LP compositions because the pool admin trusts the counterparty set. Unauthorized traders exploiting the tighter spreads cause direct LP value leakage — the pool gives away favorable prices to actors the LP never consented to trade with. This is a direct loss of LP principal and a broken core pool invariant (the allowlist guard fails open on the router path).

---

### Likelihood Explanation

The bypass is reachable whenever a pool admin takes the natural step of allowlisting the router to support router-mediated swaps for their allowlisted users. This is a plausible and common configuration. The router is a public, permissionless contract, so any user can call it. No privileged access is required beyond the admin's own (well-intentioned) allowlist entry.

---

### Recommendation

The extension must gate on the **economic actor** (the end-user), not the intermediary. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `tx.origin` or the user's address into `extensionData`; the extension decodes and checks it. This requires router cooperation and trust in the encoding.
2. **Require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router; instead, allowlisted users call `pool.swap()` directly. The router should never be added to the allowlist.

The cleaner long-term fix is to thread the original initiator address through the hook arguments so extensions can always check the true end-user independently of the call path.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Unauthorized user `alice` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. Router calls `pool.swap(recipient, zeroForOne, amount, limit, callbackData, extensionData)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
7. Alice's swap executes on the curated pool despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
