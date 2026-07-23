### Title
`SwapAllowlistExtension::beforeSwap` checks the router's address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` mediates a swap, the pool receives the router as `msg.sender` and forwards the router address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the **end user**. If the router is allowlisted on a curated pool (the natural configuration for supporting router-mediated swaps), every user — including those explicitly excluded — can bypass the per-user swap allowlist.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`**

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**What the pool passes as `sender`**

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of `swap` — as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards this unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L159-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
        sender,   // ← still the direct caller of pool.swap()
        ...
    ))
);
```

**The mismatch when the router is used**

When `MetricOmmSimpleRouter` executes a swap on behalf of a user, it calls `pool.swap(...)` directly. At that point `msg.sender` inside the pool is the **router contract**, not the end user. The extension therefore evaluates:

```
allowedSwapper[pool][router]   // ← router identity, not end-user identity
```

A pool admin who wants to support router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. Once the router is allowlisted, **any** user — including those the admin explicitly excluded — can call `MetricOmmSimpleRouter` and the check passes unconditionally.

There is no parameter in `pool.swap(...)` that lets the router forward the original user's identity; the pool's extension system is hardwired to `msg.sender`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist — the sole on-chain enforcement mechanism for the curation policy — is bypassed without any privileged action. Disallowed users can trade against LP positions that were priced and sized under the assumption of a restricted counterparty set, potentially extracting value from LPs or violating regulatory/compliance requirements the pool admin intended to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical supported periphery swap path. A pool admin who configures a swap allowlist and also wants to support router-mediated swaps for their approved users has no choice but to allowlist the router — there is no mechanism to allowlist "router + specific user" pairs. The bypass is therefore reachable in any realistic curated-pool deployment that uses the router.

---

### Recommendation

One of the following mitigations should be applied:

1. **Pass the original user through the router**: Add an optional `originator` field to the swap call or encode it in `extensionData`; have the extension decode and check it when `msg.sender` (the pool) is a known router.
2. **Check `sender` in the router, not the pool**: Have the router enforce the allowlist before calling the pool, so the pool-level extension is not the sole gate.
3. **Document and enforce that the router must never be allowlisted**: Emit a warning in the extension or factory that allowlisting a router-type contract defeats per-user gating, and provide a router variant that forwards user identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the approved trader)
  allowedSwapper[pool][router] = true  (router added so alice can use it)

Attack:
  bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
    → router calls pool.swap(...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → check: allowedSwapper[pool][router] == true  ✓
    → swap executes for bob

Result:
  bob trades on a pool that was supposed to be restricted to alice only.
  The per-user allowlist is completely bypassed.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
```text
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
```
