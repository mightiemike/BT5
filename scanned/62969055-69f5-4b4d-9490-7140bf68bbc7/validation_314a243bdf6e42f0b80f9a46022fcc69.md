### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the router's address — not the end-user's address. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user on the internet can bypass the swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the argument the pool passes — which is `msg.sender` at the pool level, i.e., whoever called `pool.swap(...)`.

When a user calls `MetricOmmSimpleRouter.exactInput` (or any `exact*` variant), the router calls `pool.swap(...)` directly. At that point, `msg.sender` inside the pool is the **router's address**, so the pool passes `sender = router_address` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router_address]`.

The pool admin must allowlist the router address to permit any router-mediated swap. Once the router is allowlisted, the check `allowedSwapper[pool][router_address]` is `true` for every call that comes through the router — regardless of who the actual end-user is. A user who is explicitly **not** on the allowlist can call `router.exactInput(pool, ...)` and the extension will pass because it sees the router, not the user.

The `DepositAllowlistExtension` does not share this flaw because it gates the `owner` parameter (an explicit position-owner address), not `sender`. The swap path has no equivalent explicit "real swapper" parameter. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Direct policy bypass on curated pools.** The swap allowlist exists to restrict which addresses may trade on a given pool (e.g., KYC-gated, institutional-only, or whitelist-only pools). Once the router is allowlisted — which is required for normal UX — any unprivileged user can trade on the pool by routing through `MetricOmmSimpleRouter`. The pool admin's curation is completely nullified. Depending on the pool's purpose, this can result in:

- Unauthorized users draining LP value through adversarial swaps on a pool designed for a closed set of counterparties.
- Regulatory or compliance failure if the pool is gated for legal reasons.
- Loss of LP principal if the pool's pricing or liquidity assumptions depend on a controlled set of swappers.

Severity: **High** — direct loss of access-control invariant with fund-impacting consequences for LPs on curated pools. [4](#0-3) 

---

### Likelihood Explanation

**High.** The attack requires no special privilege:

1. The router (`MetricOmmSimpleRouter`) is a standard public periphery contract.
2. Any pool admin who wants users to be able to swap via the router must allowlist the router address — this is the expected operational pattern.
3. Once the router is allowlisted, any user can call `router.exactInput(...)` targeting the curated pool. No admin action, no special token, no flash loan needed.

The only scenario where the bypass does not apply is if the pool admin intentionally blocks the router and forces all swappers to call the pool directly — but this breaks normal UX and is not the documented design intent. [5](#0-4) 

---

### Recommendation

The `SwapAllowlistExtension` must gate the **end-user**, not the intermediary. Two sound approaches:

1. **Check `sender` and require the router to forward the real user identity.** The pool's `swap` function could accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` at the router level. The extension then checks that explicit address. This requires a pool-level interface change.

2. **Allowlist the router as a trusted forwarder and require it to pass the real user in `extensionData`.** The extension reads the real user from `extensionData` when `sender` is a known trusted forwarder, and checks that address instead. This is a purely extension-level fix but requires the router to cooperate.

3. **Do not allowlist the router; require direct pool calls for allowlisted pools.** Document this constraint clearly. This is the safest short-term mitigation but breaks composability. [1](#0-0) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInput(pool, ...)
  - Router calls pool.swap(recipient=alice, ...)
  - Pool passes sender=router_address to _beforeSwap
  - Extension checks: allowedSwapper[pool][router_address] == true  → PASSES
  - Alice's swap executes on the curated pool despite not being allowlisted

Expected:
  - Extension should check allowedSwapper[pool][alice] == false → REVERT NotAllowedToSwap

Contrast (direct call):
  - alice calls pool.swap(...) directly
  - Pool passes sender=alice to _beforeSwap
  - Extension checks: allowedSwapper[pool][alice] == false → REVERTS correctly
``` [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
