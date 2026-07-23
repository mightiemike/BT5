### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Restrictions via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (a natural action when they want allowlisted users to be able to use the router), every unprivileged user can bypass the per-user restriction by routing through the router, executing swaps on a pool that was intended to be private.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of `pool.swap()`.

In `MetricOmmPool.swap()`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle()` is called by an end user, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The pool receives `msg.sender = router`, passes `sender = router` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router]`. The original end user's address is never visible to the extension — it is not forwarded anywhere in the call chain.

This creates an irreconcilable conflict for the pool admin:

| Admin intent | Admin action | Actual result |
|---|---|---|
| Allow specific users to swap directly | `setAllowedToSwap(pool, userA, true)` | userA can swap directly ✓ |
| Allow those same users to use the router | `setAllowedToSwap(pool, router, true)` | **Every user can now swap via router** ✗ |
| Block all other users | (no action) | Bypassed by routing ✗ |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from using the router.

---

### Impact Explanation

Any unprivileged user can swap on a pool that is supposed to be restricted to specific counterparties by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`). Consequences:

- **Direct LP fund loss**: Unauthorized traders extract value from LP bins at oracle-derived prices. Every swap that the pool admin did not intend to permit drains LP principal through the spread and notional fee mechanism.
- **Broken core pool functionality**: The allowlist guard — the only mechanism for restricting swap access — is rendered ineffective for any pool that needs to support router-mediated swaps.
- **Pool insolvency risk**: If the oracle price is stale or the pool is near a stop-loss boundary, unauthorized swaps can push the pool into a state where LP claims cannot be fully covered.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is a natural and expected admin action: a pool admin who wants to allow their allowlisted users to use the router will call `setAllowedToSwap(pool, router, true)`. The admin has no way to know that this simultaneously opens the pool to all router users. The router is a public, permissionless contract deployed by the protocol itself, so allowlisting it is a reasonable and foreseeable configuration choice.

---

### Recommendation

The `SwapAllowlistExtension` must check the original end user's address, not the router's address. Two approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and verify it. The extension would then check `allowedSwapper[pool][decodedUser]` instead of `allowedSwapper[pool][sender]`.

2. **Dedicated router integration**: Have the router expose a `swapper()` view that returns the current end user (stored in transient storage at entry), and have the extension call `IMetricOmmSimpleRouter(sender).swapper()` when `sender` is a known router address.

Either approach ensures the allowlist gates the economically relevant actor, not the intermediary.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, userA, true)      // intended allowlisted user
  admin calls setAllowedToSwap(pool, router, true)     // to let userA use the router
  admin adds liquidity to the pool

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: 1000,
        recipient: userB,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=userB, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← check passes
        → swap executes, userB receives tokens from LP bins

Result:
  userB successfully swaps on a pool they are not allowlisted for.
  LP funds are drained by an unauthorized counterparty.
``` [1](#0-0) [2](#0-1) 
<cite repo="Thankgoddavid56/2026-07-metric-dev-oyakhil-main--007" path="metric-periphery/contracts/MetricOmmSimpleRouter.sol" start="

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
