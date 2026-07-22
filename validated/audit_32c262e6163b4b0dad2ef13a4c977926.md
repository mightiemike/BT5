The call chain is fully traceable. Here is the complete analysis:

---

## Call Chain Trace

**Direct swap:**
```
User → MetricOmmPool::swap()
  msg.sender = user
  _beforeSwap(msg.sender=user, ...)
  → ExtensionCalling::_beforeSwap encodes sender=user
  → SwapAllowlistExtension::beforeSwap(sender=user)
    msg.sender = pool, sender = user
    check: allowedSwapper[pool][user]  ✓ correct
```

**Router swap (`exactInputSingle` / `exactInput` / `exactOutput`):**
```
User → MetricOmmSimpleRouter::exactInputSingle()
  → IMetricOmmPoolActions(pool).swap(...)
      msg.sender in pool = ROUTER address
  _beforeSwap(msg.sender=router, ...)
  → ExtensionCalling::_beforeSwap encodes sender=router
  → SwapAllowlistExtension::beforeSwap(sender=router)
    msg.sender = pool, sender = ROUTER
    check: allowedSwapper[pool][ROUTER]  ✗ wrong identity
```

The pool passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling` forwards that value verbatim to the extension: [2](#0-1) 

The hook checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = the immediate pool caller: [3](#0-2) 

When the router calls the pool, `sender` is the router address, not the originating user.

---

## Two Resulting Failure Modes

**Mode A — DoS for allowlisted users via router:**
If the admin has not allowlisted the router, every router-mediated swap reverts with `NotAllowedToSwap()`, even for users who are individually allowlisted. Allowlisted users are forced to call the pool directly, making the router unusable for any allowlist-gated pool.

**Mode B — Complete allowlist bypass:**
If the admin allowlists the router address to restore router functionality for their allowlisted users (`allowedSwapper[pool][router] = true`), then *any* unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The hook passes because `sender = router` is allowlisted, regardless of who the actual originating user is.

The router's `exactInputSingle` stores the payer in transient storage for the callback, but this payer identity is never forwarded to the pool's `swap()` call or to the extension: [4](#0-3) 

There is no mechanism by which the extension can recover the true originating user.

---

### Title
SwapAllowlistExtension Receives Router Address as `sender`, Enabling Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` checks the immediate caller of `MetricOmmPool::swap` as the swapper identity. When swaps are routed through `MetricOmmSimpleRouter`, the immediate caller is the router contract, not the originating user. This corrupts the identity the hook was designed to gate.

### Finding Description
`MetricOmmPool::swap` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension::beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router — instead of the actual user. The originating user's address is stored only in the router's transient callback context and is never surfaced to the pool or its extensions. [5](#0-4) 

### Impact Explanation
- **Mode A (DoS):** Allowlisted users cannot swap through the router on any allowlist-gated pool. Core swap functionality is broken for the intended user set.
- **Mode B (bypass):** If the admin allowlists the router to restore router access, every unprivileged user can bypass the allowlist by calling any `exact*` function on the router. The protection silently fails open for the entire pool.

Mode B constitutes a broken access-control boundary: an unprivileged path (`MetricOmmSimpleRouter::exactInputSingle/exactInput/exactOutput/exactOutputSingle`) bypasses a configured pool-level guard without any privileged action by the attacker.

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router is affected. Mode A manifests immediately on first router swap. Mode B manifests as soon as the admin attempts to restore router access by allowlisting the router address, which is the only available workaround.

### Recommendation
The originating user's address must be propagated to the extension. Options include:
1. Passing the true originating user through `extensionData` (requires router cooperation and extension parsing).
2. Adding a separate `originator` field to the hook signature that the pool populates from a trusted source.
3. Documenting explicitly that `SwapAllowlistExtension` is incompatible with any intermediary contract and enforcing this at the factory level.

### Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true
  allowedSwapper[pool][attacker] = false
  allowAllSwappers[pool] = false

Step 1 (Mode A):
  alice calls router.exactInputSingle({pool: pool, ...})
  → pool.swap(msg.sender=router)
  → beforeSwap(sender=router)
  → allowedSwapper[pool][router] == false → revert NotAllowedToSwap
  Alice cannot use the router despite being allowlisted.

Step 2 (admin workaround):
  admin calls setAllowedToSwap(pool, router, true)

Step 3 (Mode B):
  attacker calls router.exactInputSingle({pool: pool, ...})
  → pool.swap(msg.sender=router)
  → beforeSwap(sender=router)
  → allowedSwapper[pool][router] == true → passes
  Attacker swaps on a pool they were never allowlisted for.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
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
  }
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
