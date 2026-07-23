### Title
`SwapAllowlistExtension` checks `sender` (router address) instead of `recipient` (actual end-user), allowing any user to bypass the per-pool swap allowlist by routing through an allowlisted router — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the pool call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the actual end-user. A pool admin who allowlists the router (required for any router-based swap to succeed) inadvertently opens the pool to every user, defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

`MetricOmmPool` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

The `SwapAllowlistExtension` then gates on that `sender` value while silently discarding the `recipient` (the actual end-user who receives output tokens): [2](#0-1) 

The check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` inside the extension is the pool; `sender` is whoever called the pool. When `MetricOmmSimpleRouter` calls the pool, `sender` = router address. The extension therefore asks: *"is the router allowlisted?"* — not *"is the end-user allowlisted?"*

For a curated pool to be usable via the router at all, the pool admin must add the router to `allowedSwapper[pool][router]`. The moment that entry exists, every user who routes through the router passes the guard, regardless of whether they are individually permitted.

Compare with `DepositAllowlistExtension`, which correctly checks `owner` (the LP position owner, explicitly passed by the caller and enforced by the pool's operator pattern): [3](#0-2) 

The deposit extension gates the right actor; the swap extension gates the wrong one.

The pool's `addLiquidity` NatSpec confirms the intended operator/owner separation: [4](#0-3) 

No equivalent separation exists for swaps: the pool never forwards the true initiating user's address to the extension — only `msg.sender` (the router) and `recipient` (ignored by the extension).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional participants) is fully open to any address that routes through `MetricOmmSimpleRouter`. The allowlist guard is silently bypassed on every router-mediated swap. Any non-allowlisted user can trade on the restricted pool, violating the pool admin's access-control invariant and the protocol's curation guarantee. [5](#0-4) 

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary supported swap entry point in the periphery.
- Any curated pool that intends to use the router must allowlist the router address; there is no other option.
- Once the router is allowlisted, the bypass is unconditional and requires no special privileges — any EOA or contract can call the router.
- The pool admin has no way to simultaneously allow router-based swaps and enforce per-user gating with the current extension design. [2](#0-1) 

---

### Recommendation

Change `SwapAllowlistExtension.beforeSwap` to gate on `recipient` (the actual end-user) rather than `sender` (the router/caller):

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, document explicitly that the extension gates at the caller (router) level only, and provide a separate per-user extension that checks `recipient` for pools that require individual user gating. [2](#0-1) 

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` as the `beforeSwap` extension.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — required for any router-based swap.
3. Non-allowlisted user `Alice` calls `MetricOmmSimpleRouter.swap(pool, recipient=Alice, ...)`.
4. Router calls `pool.swap(recipient=Alice, ...)` — pool sets `sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Alice, who was never individually allowlisted, successfully trades on the curated pool.

The `FullMetricExtensionTest` confirms the router-mediated swap path is the intended production flow: [6](#0-5) 

The test allowlists `callers[0]` (a `TestCaller` contract acting as the router analog) — demonstrating that the check fires on the caller address, not the end-user `users[0]` passed as `recipient`. [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-42)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmExtensions} from "@metric-core/interfaces/extensions/IMetricOmmExtensions.sol";
import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {ISwapAllowlistExtension} from "../interfaces/extensions/ISwapAllowlistExtension.sol";
import {BaseMetricExtension} from "./base/BaseMetricExtension.sol";

/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-150)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
