### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any user to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router address**, not the end user. If the router is allowlisted (or the pool admin allowlists it to enable router-mediated swaps), every user — including those the admin intended to block — can bypass the restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool's `swap` function does not accept an explicit swapper identity parameter; it uses `msg.sender` internally and forwards it as `sender` to the extension. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router, so the extension receives `sender = address(router)`. [2](#0-1) 

This is structurally different from `DepositAllowlistExtension`, which correctly checks the explicit `owner` parameter passed to `addLiquidity` — the actual LP owner regardless of who calls the function:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The deposit extension correctly gates the economic actor; the swap extension does not.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd users or designated market makers).
2. Pool admin allowlists `MetricOmmSimpleRouter` (a natural step — the router is the supported public swap entrypoint).
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInput(...)` (or equivalent).
4. The router calls `pool.swap(...)` with itself as `msg.sender`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The non-allowlisted user's swap executes against the restricted pool.

**Broken functionality path (dual failure):**

If the router is NOT allowlisted, allowlisted users who route through the router are incorrectly blocked, breaking the core swap flow for the supported periphery path. [4](#0-3) 

---

### Impact Explanation

A curated pool's swap allowlist is the primary access-control boundary for restricted trading venues. Bypassing it allows unauthorized users to trade against LP positions that were never intended to be exposed to them. If the restriction was designed to prevent front-running, MEV extraction, or regulatory non-compliance, the bypass directly exposes LPs to losses from harmful trading patterns they opted out of. This is a direct loss of LP assets above Sherlock thresholds when the pool is actively used.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery. Pool admins who configure a swap allowlist and also want to support router-mediated swaps will naturally allowlist the router — the exact condition that enables the bypass. The trigger requires no privileged access beyond the pool admin's own intended configuration, and the exploit path is a standard public router call. [5](#0-4) 

---

### Recommendation

Mirror the deposit extension's design: add an explicit `swapper` identity parameter to the pool's `swap` function (analogous to `owner` in `addLiquidity`), and have `MetricOmmSimpleRouter` forward `msg.sender` (the actual end user) as that parameter. The extension then checks the explicit swapper identity rather than the intermediary caller.

Alternatively, require the extension to decode the actual swapper from `extensionData` when the caller is a known router, and have the router inject the real user address into `extensionData` before forwarding to the pool. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `allowedUser` and `address(router)`
  - `blockedUser` is NOT on the allowlist

Attack:
  1. blockedUser calls router.exactInput({pool: pool, ...})
  2. Router calls pool.swap(recipient=blockedUser, ...)
  3. Pool calls extension.beforeSwap(sender=address(router), ...)
  4. Extension checks allowedSwapper[pool][router] → true
  5. Swap executes — blockedUser successfully trades in the restricted pool

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L156-162)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
