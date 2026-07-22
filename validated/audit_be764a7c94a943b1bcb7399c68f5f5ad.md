### Title
SwapAllowlistExtension Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the original user. A pool admin who allowlists the router (required for any allowlisted user to use the standard periphery) simultaneously opens the gate for every non-allowlisted user, completely defeating the per-user access control.

---

### Finding Description

`BaseMetricExtension` defines `beforeSwap` with an `onlyPool` modifier that validates `msg.sender` against the factory registry. [1](#0-0) 

`SwapAllowlistExtension` overrides `beforeSwap` as a `view` function **without** the `onlyPool` modifier, and gates access by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool and `sender` is the address the pool received as its own `msg.sender`: [2](#0-1) 

The allowlist is keyed `allowedSwapper[pool][swapper]` and managed per-pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap()` directly. The pool therefore passes `sender = router_address` to the extension hook. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps blocked, even for allowlisted users |
| Allowlist the router | Every user — allowlisted or not — can bypass the per-user gate by routing through the router |

The `IMetricOmmExtensions.beforeSwap` interface confirms `sender` is the first positional argument passed by the pool, which is `msg.sender` of the `swap()` call: [4](#0-3) 

The factory's `isPool` registry exists precisely to validate pool identity, but the extension's access-control logic never uses it to validate the *original swapper*: [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can swap in a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. If the pool offers subsidized spreads, restricted pricing, or is intended for a closed set of counterparties, unauthorized users can extract value from LP positions or trade at rates the pool admin never intended to offer them. This breaks the admin-boundary invariant: an unprivileged path (the public router) bypasses a configured access-control guard.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard user-facing entry point. Any pool admin who wants allowlisted users to access the pool via the router must allowlist the router address, which immediately opens the bypass to everyone. The attacker needs no special privileges, no custom contract, and no capital beyond the swap amount.

---

### Recommendation

The extension must gate on the **economically relevant actor**, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user in `extensionData`**: The router encodes the original `msg.sender` in `extensionData`; the extension decodes and checks it. The pool admin configures the extension to trust the router as a forwarding agent.
2. **Check `tx.origin` with a trusted-router guard**: Only accept `tx.origin` when `msg.sender` (the pool's direct caller) is a factory-registered router, preventing arbitrary contract abuse while preserving the original-user identity.

Additionally, `SwapAllowlistExtension.beforeSwap` should restore the `onlyPool` modifier (dropped from the base-class override) so that only factory-registered pools can invoke the hook at all: [6](#0-5) 

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack
──────
4. Bob (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       tokenIn, tokenOut, pool, amountIn, minOut, deadline
   )
   → router calls pool.swap(recipient=bob, ...)
   → pool calls extension.beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for Bob despite Bob never being allowlisted

Result
──────
Bob swaps successfully in a pool the admin intended to restrict to alice only.
``` [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L148-151)
```text
  /// @inheritdoc IMetricOmmPoolFactory
  function isPool(address pool) external view override returns (bool) {
    return poolToIdx[pool] != 0;
  }
```
