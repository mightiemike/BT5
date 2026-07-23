### Title
SwapAllowlistExtension gates the router address instead of the real user, allowing any non-allowlisted trader to bypass a curated pool's swap guard via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` at the pool call boundary. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the real user. If the router is allowlisted (a natural admin choice for a trusted periphery contract), every non-allowlisted user can bypass the curated pool's swap gate by routing through it.

---

### Finding Description

`MetricOmmPool.swap` always passes its own `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist, using `msg.sender` (the pool) as the namespace key: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)` directly. At that call boundary, `msg.sender` inside the pool is the **router address**, not the originating user. The extension therefore evaluates:

```
allowedSwapper[pool][router]   // checked
allowedSwapper[pool][user]     // never consulted
```

A pool admin who allowlists the router as a trusted periphery contract (the expected operational pattern) inadvertently opens the gate for every user who routes through it, regardless of whether that user is individually allowlisted.

The allowlist storage and setter confirm the design intent is per-user gating, not per-intermediary gating: [3](#0-2) 

The `ISwapAllowlistExtension` interface reinforces this: `setAllowedToSwap(pool, swapper, allowed)` is clearly meant to gate individual swapper addresses, not routers: [4](#0-3) 

The analog to the RLN bug is exact: in RLN, `slashCommitments[account][hash]` was not keyed by `msg.sender`, so any slasher could act on another's hash. Here, `allowedSwapper[pool][sender]` is not keyed by the real user — it is keyed by the intermediary — so any user can act through the router and inherit the router's allowlist status.

---

### Impact Explanation

**Severity: High**

A non-allowlisted user on a curated pool can execute swaps that the pool admin explicitly intended to block. The pool receives real token input and pays real token output at oracle prices; there is no slippage protection from the allowlist once it is bypassed. LP funds are exposed to traders the pool was designed to exclude, which is a direct loss of the curation guarantee and can result in adverse-selection losses for LPs on pools that rely on the allowlist as their primary access control.

---

### Likelihood Explanation

**Likelihood: High**

- `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery layer.
- A pool admin who deploys a curated pool and wants users to access it via the standard router will naturally allowlist the router address.
- No special privilege or unusual configuration is required; the bypass is reachable by any user who calls the router instead of the pool directly.
- The pool's `swap` function provides no mechanism to forward the originating user's identity; the router has no way to pass it either, so the misbinding is structural.

---

### Recommendation

The extension must resolve the real user identity rather than trusting the `sender` argument when the caller is a known intermediary. Two sound approaches:

1. **Pass the real user through the router**: Add a `swapper` parameter to the router's `exact*` functions and forward it as part of `extensionData`. The extension decodes it and checks `allowedSwapper[pool][swapper]` only when `sender` is a recognized router.

2. **Key the allowlist on the economic actor, not the call-boundary actor**: Redesign the extension to accept an explicit `swapper` field in `extensionData` that the router populates with `msg.sender` before calling the pool. The extension ignores the `sender` argument entirely and checks only the decoded `swapper`.

Either approach mirrors the RLN fix: add the real actor as an explicit key so the guard cannot be inherited by an intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router as trusted periphery
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  1. Alice (non-allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
  2. Router calls pool.swap(recipient=alice, ...)
  3. Pool sets sender = msg.sender = router_address
  4. Pool calls extension.beforeSwap(sender=router, ...)
  5. Extension checks: allowedSwapper[pool][router] == true  → passes
  6. Swap executes; Alice receives output tokens

Result:
  Alice successfully swaps on a pool she is not allowlisted for.
  The allowlist guard is completely bypassed via the public router.
``` [2](#0-1) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/interfaces/extensions/ISwapAllowlistExtension.sol (L14-16)
```text
  function setAllowedToSwap(address pool, address swapper, bool allowed) external;

  function setAllowAllSwappers(address pool, bool allowed) external;
```
