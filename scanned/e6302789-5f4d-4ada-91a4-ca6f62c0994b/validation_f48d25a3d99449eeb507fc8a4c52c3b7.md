### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end-user. If the router is allowlisted (which it must be for router-mediated swaps to work at all), every unprivileged user can bypass the per-pool swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool` passes its own `msg.sender` as the `sender` argument to every extension hook. This is visible in `simulateSwapAndRevert` and is the same pattern used in the regular `swap` entry point:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end-user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every registered extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool allowlist:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender` is the router. The extension therefore receives `sender = router` and checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For the router to be usable at all on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, **every** caller of the router — including addresses the admin explicitly excluded — passes the guard, because the extension never sees the end-user's address.

The allowlist mapping and setter confirm the per-pool, per-address design intent:

```solidity
mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;

function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
}
``` [4](#0-3) 

The admin intends to gate individual swappers, but the implementation gates the direct caller of `pool.swap()`. These two identities diverge whenever the public router is in the call path.

---

### Impact Explanation

Any user who is not on the allowlist can execute swaps on a permissioned pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The router is the `sender` the extension sees; if the router is allowlisted, the guard is fully neutralised for all users. This breaks the core security model of permissioned pools: unauthorized counterparties can trade against LP positions, extract value, or front-run in pools that were designed to be restricted. This is a direct loss-of-control over LP assets and constitutes a broken core pool functionality / admin-boundary break above Sherlock thresholds.

---

### Likelihood Explanation

The trigger requires only that:
1. A pool is deployed with `SwapAllowlistExtension` configured.
2. The pool admin adds `MetricOmmSimpleRouter` to the allowlist (necessary for any router-mediated swap to work).
3. An unprivileged user calls the router.

All three conditions are part of the normal, documented usage path. No privileged access, no malicious setup, and no non-standard tokens are required. Any public user can execute this.

---

### Recommendation

The extension must gate the economically relevant actor — the end-user — not the intermediary router. Two complementary fixes:

1. **Router-level forwarding**: `MetricOmmSimpleRouter` should encode the original `msg.sender` into `extensionData` and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a known router.
2. **Pool-level originator tracking**: Alternatively, the pool can accept an explicit `originator` parameter (separate from `sender`) that periphery contracts populate with the end-user's address, and extensions check `originator` instead of `sender`.

Until one of these is in place, pools that need a real swap allowlist must not allowlist the router, which makes the router unusable for those pools.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // router must be allowed for normal use
  admin calls setAllowedToSwap(pool, alice, true)     // alice is the intended allowlisted user
  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
    → router calls pool.swap(bob_as_recipient, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being on the allowlist

Result:
  bob successfully swaps on a pool he should be blocked from.
  The allowlist provides zero protection for router-mediated swaps.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-19)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
