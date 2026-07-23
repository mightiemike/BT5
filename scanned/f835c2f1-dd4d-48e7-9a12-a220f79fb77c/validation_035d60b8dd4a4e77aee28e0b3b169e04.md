### Title
SwapAllowlistExtension Gates Direct Pool Caller Not Ultimate User — Router Allowlisting Opens Gate to All Swappers - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`, not the ultimate user. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router address. A pool admin who allowlists the router (to permit allowlisted users to use it for better UX) inadvertently opens the gate to every user, because the router is a shared public contract with no per-user gating of its own.

### Finding Description
In `SwapAllowlistExtension.beforeSwap`:

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

`msg.sender` is the pool (correct), and `sender` is the first parameter forwarded through the call chain:

`MetricOmmPool.swap` passes `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes it as the first argument to the extension: [2](#0-1) 

So `sender` in `beforeSwap` is always the direct caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()`, making `sender = router`. The allowlist check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The allowlist admin setter: [3](#0-2) 

is designed for per-address gating, but the hook check: [4](#0-3) 

collapses all router users into a single identity (`router`). Once the router is allowlisted, the per-user boundary is gone.

### Impact Explanation
Any non-allowlisted user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. The pool admin's intended per-user access control is silently nullified. For pools designed as private or KYC-gated venues, this allows unauthorized participants to execute swaps, violating the admin-configured boundary — an admin-boundary break by an unprivileged path.

### Likelihood Explanation
The bypass requires the router to be allowlisted. A pool admin who wants allowlisted users to use the router (a reasonable UX expectation) will allowlist the router, inadvertently opening the gate to all users. The misconfiguration is non-obvious: the admin believes they are allowlisting "the router for allowlisted users" but are actually allowlisting "the router for everyone." The protocol's own audit target list explicitly flags this concern: [5](#0-4) 

### Recommendation
Gate by the ultimate user, not the direct caller. Concrete options:
1. Have the router populate `extensionData` with the original `msg.sender` and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router.
2. Add a dedicated `IMetricOmmSwapCallback`-style identity field that the router signs and the extension verifies.
3. At minimum, document explicitly that allowlisting the router address opens the gate to all users, and advise pool admins to never allowlist shared periphery contracts when per-user gating is required.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (to let alice use the router).
3. `bob` (non-allowlisted) calls `MetricOmmSimpleRouter.exactInput(pool, ...)`.
4. The router calls `pool.swap(...)` — `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes successfully, bypassing the per-user allowlist entirely.

The root cause is in `SwapAllowlistExtension.beforeSwap` at: [6](#0-5)

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
