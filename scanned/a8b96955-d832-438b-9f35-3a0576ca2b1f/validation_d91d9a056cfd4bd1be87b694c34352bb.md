### Title
`SwapAllowlistExtension` gates the router intermediary instead of the end user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every registered extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()`, making `msg.sender` to the pool the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps always revert — router is unusable for this pool |
| **Allowlist the router** | Every user on the network can bypass the per-user allowlist by routing through the public router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user gating.

The analog to the external report is exact: just as `StakedToken` uses `balanceOf()` (the externally-manipulable value) instead of an internal tracker, `SwapAllowlistExtension` uses `sender` (the externally-substitutable intermediary) instead of the actual economic actor. In both cases the guard reads the wrong value, and an unprivileged actor can exploit the gap.

---

### Impact Explanation

Any user can execute swaps on a pool that the admin intended to restrict to a specific allowlist, by routing through the public `MetricOmmSimpleRouter`. This breaks the admin-boundary invariant: an unprivileged path (`MetricOmmSimpleRouter → pool.swap`) bypasses a configured access control gate. Depending on the pool's purpose (e.g., private institutional pool, KYC-gated pool), unauthorized swaps can drain LP assets at oracle-derived prices, constituting a direct loss of LP principal.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to have allowlisted the router — a natural and expected configuration for any pool that wants to support the standard periphery. Once that configuration is in place, the bypass is trivially reachable by any user with no special privileges, no front-running, and no capital requirement beyond the swap input.

---

### Recommendation

The extension must check the **end user**, not the intermediary. Two viable approaches:

1. **Check `recipient` instead of `sender`**: For direct swaps `recipient` is typically the user; for router swaps the router sets `recipient` to the actual user. However, `recipient` can be set to any address, so this is not fully reliable either.

2. **Forward the original caller via `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address. This requires a coordinated change to the router and extension.

3. **Allowlist at the router level**: The router enforces its own per-user allowlist before calling `pool.swap()`, and the pool-level extension only allowlists the router. This moves the gate to the correct layer.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension registered on BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so router swaps work
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=alice, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — alice receives output tokens despite not being allowlisted

Direct swap check (for comparison):
  1. Alice calls pool.swap() directly
  2. Pool calls _beforeSwap(sender=alice, ...)
  3. SwapAllowlistExtension checks allowedSwapper[pool][alice] == false  ✗ → revert
```

The allowlist is enforced for direct callers but silently bypassed for any user who routes through the public `MetricOmmSimpleRouter`. [4](#0-3) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
