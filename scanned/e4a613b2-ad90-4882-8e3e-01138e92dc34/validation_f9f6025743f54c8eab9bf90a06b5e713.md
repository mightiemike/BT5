### Title
`SwapAllowlistExtension.beforeSwap` checks the router's identity instead of the actual swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the value the pool passes as the first argument, which is always `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the router is allowlisted (a natural admin step to enable router-mediated swaps), any user can bypass the per-pool swap allowlist entirely.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

And `sender` is set in `MetricOmmPool.swap` as:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, making `msg.sender` inside the pool equal to the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to enable router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. Doing so silently opens the gate to every user, because the check never reaches the actual initiator's address.

This is the direct analog to the external bug: just as `get_first` returned the highest-ICR (safest) trove instead of the lowest-ICR (riskiest) one, `sender` here resolves to the router (the intermediary) instead of the actual swapper (the economically relevant actor the guard was designed to vet).

---

### Impact Explanation

The swap allowlist guard is the primary access-control mechanism for restricted pools. Its bypass allows any unprivileged address to execute swaps in a pool that the admin intended to gate to a specific set of counterparties (e.g., KYC-verified traders, institutional partners). Depending on pool design, this can expose LP capital to adversarial flow, front-running, or regulatory non-compliance — all of which constitute broken core pool functionality with direct fund-impacting consequences for LPs.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected configuration step: without it, allowlisted users cannot use the router at all, making the allowlist incompatible with the primary periphery entry point. Any pool that combines `SwapAllowlistExtension` with router support is therefore vulnerable by construction.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Transient-storage attribution**: The router writes the original caller into a transient slot before calling the pool; the extension reads it. This is consistent with the protocol's existing use of EIP-1153 transient storage for callback context.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `extension1`, `beforeSwap` order set to call extension 1.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a necessary step to allow any router-mediated swap.
3. User B (address never added to the allowlist) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. The router calls `pool.swap(recipient, ...)` — inside the pool, `msg.sender == router`.
5. `_beforeSwap(msg.sender=router, ...)` is called; the extension receives `sender = router`.
6. `allowedSwapper[pool][router] == true` → guard passes.
7. User B's swap executes in a pool they were never authorized to access. [1](#0-0) [2](#0-1) 
<cite repo="Thankgoddavid56/2026-07-metric-dev-oyakhil-main--022" path="metric-core/contracts/Extension

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
