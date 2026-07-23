### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the router is allowlisted (a natural admin action to permit router-mediated swaps), every user — including those explicitly denied — bypasses the allowlist gate.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct pool-key) and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender` at call time.

`MetricOmmPool.swap` (and `simulateSwapAndRevert`) passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the hook:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls the pool, the pool's `msg.sender` is the router contract:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

So the extension's effective check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's identity is never consulted.

Additionally, `SwapAllowlistExtension.beforeSwap` overrides the base class but **drops the `onlyPool` modifier** present in `BaseMetricExtension.beforeSwap`:

```solidity
// BaseMetricExtension — has onlyPool
function beforeSwap(...) external virtual onlyPool returns (bytes4) { ... }

// SwapAllowlistExtension — no onlyPool, view
function beforeSwap(address sender, ...) external view override returns (bytes4) { ... }
``` [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified users, protocol partners). If the admin also allowlists `MetricOmmSimpleRouter` — a natural action so that allowlisted users can use the standard periphery — then **every user on-chain can bypass the allowlist** by calling any `exact*` function on the router. The router's address passes the check; the actual caller's address is never examined. LP funds in the pool are exposed to swaps from actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The trigger requires only:
1. A pool configured with `SwapAllowlistExtension` (a supported, documented extension).
2. The pool admin allowlisting `MetricOmmSimpleRouter` (the canonical periphery swap path, expected for normal usage).
3. Any disallowed user calling `router.exactInputSingle` or `router.exactInput`.

All three conditions are reachable by unprivileged actors using only the documented public interface. No special tokens, malicious setup, or privileged access is required.

---

### Recommendation

The pool must forward the **original user's address** through the call chain so the extension can gate on the economically relevant actor. Two complementary fixes:

1. **Pool-level**: Add a `msgSender` parameter to `swap()` that the pool populates with `msg.sender` and passes separately from `sender` (or rename the existing `sender` to make its semantics explicit). Alternatively, store the original caller in transient storage before the extension call.

2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should re-add the `onlyPool` modifier (inherited from `BaseMetricExtension`) and check a separately forwarded original-caller field rather than the `sender` argument, which is the direct pool caller (the router).

A minimal fix: restore `onlyPool` on `SwapAllowlistExtension.beforeSwap` and document that `sender` is the direct pool caller, then require pool admins to allowlist the router and rely on a separate user-level check inside the router — but this still does not gate the actual user. The correct fix is to thread the original EOA through the call.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is denied

4. alice calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes — alice's trade goes through despite being denied

5. alice directly calls pool.swap(...)
   → pool calls _beforeSwap(sender=alice, ...)
   → extension checks allowedSwapper[pool][alice] == false  ✗
   → revert NotAllowedToSwap — correctly blocked
```

The allowlist is enforced only on direct pool calls; the router path silently substitutes the router's address for the user's address, making the guard ineffective for any router-mediated swap.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
