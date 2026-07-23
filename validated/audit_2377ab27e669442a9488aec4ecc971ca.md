The call chain is fully traceable. Here is the analysis:

## Call Chain Trace

```
User (Alice) → MetricOmmSimpleRouter::exactInputSingle
  → MetricOmmPool::swap(recipient, ...) [msg.sender = router]
    → _beforeSwap(msg.sender=router, ...)
      → ExtensionCalling::_beforeSwap(sender=router, ...)
        → SwapAllowlistExtension::beforeSwap(sender=router, ...)
          → allowedSwapper[msg.sender=pool][sender=router]  ← checks ROUTER, not Alice
```

**The hook always sees the router's address as `sender`, never the end user's address.**

---

### Title
Router Identity Substitution Bypasses SwapAllowlistExtension Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The hook therefore checks whether the **router** is allowlisted, not whether the individual user is allowlisted. If the pool admin allowlists the router to permit router-mediated swaps, every unprivileged user on the network can bypass the per-user allowlist by routing through the router.

### Finding Description

In `MetricOmmPool::swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

The router does **not** pass the original `msg.sender` (Alice) into the pool call. The pool receives `msg.sender = router`. The hook therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][Alice]`.

The allowlist mapping is keyed by pool and swapper: [5](#0-4) 

### Impact Explanation

Two concrete failure modes arise:

**Mode 1 — Allowlist bypass (high impact):** The pool admin allowlists the router address (a natural operational choice to let users access the pool via the official router). Any unprivileged user can now call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the hook passes because `allowedSwapper[pool][router] == true`. The per-user allowlist is completely nullified for router-mediated paths. Any user the admin intended to block can swap freely.

**Mode 2 — Legitimate user blocked (medium impact):** The pool admin allowlists specific EOAs (Alice, Bob) but not the router. Alice's direct `pool.swap()` succeeds. Alice's `router.exactInputSingle()` reverts with `NotAllowedToSwap` because the hook checks the router address, which is not allowlisted. Core swap functionality is broken for allowlisted users who use the router.

Both modes break the invariant stated in the extension's own NatSpec: *"Gates `swap` by swapper address, per pool."*

### Likelihood Explanation

The router is the standard user-facing entry point. Any pool that uses `SwapAllowlistExtension` and also expects users to interact via the router will encounter one of the two failure modes. No special privileges, flash loans, or oracle manipulation are required — two ordinary transactions suffice (e.g., tx1: admin allowlists router; tx2: attacker routes through router).

### Recommendation

Pass the **originating user** through the call chain. One approach: add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender`, and have the hook check that value. Alternatively, the extension can accept an `extensionData`-encoded address for the true swapper and verify it against a router-signed proof. At minimum, document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

### Proof of Concept

```
// Setup
pool.setAllowedToSwap(pool, router, true);   // admin allowlists router
pool.setAllowedToSwap(pool, alice, false);    // alice is NOT allowlisted

// Attack (two public transactions)
// Tx 1: attacker (not alice) calls router
router.exactInputSingle({pool: pool, ..., recipient: attacker});
// → pool.swap(msg.sender=router) → beforeSwap(sender=router)
// → allowedSwapper[pool][router] == true → PASSES
// Attacker swaps in a pool they were never meant to access.

// Tx 2: alice (legitimately allowlisted EOA) tries router
router.exactInputSingle({pool: pool, ..., recipient: alice});
// → beforeSwap(sender=router) → allowedSwapper[pool][router] == true → PASSES
// Alice also bypasses her own per-address entry (irrelevant here, but shows
// the check is on the router, not the user).
```

Direct swap by a non-allowlisted EOA still reverts correctly — the bypass only works through the router, confirming the identity substitution is the root cause.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
