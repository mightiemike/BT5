### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the identity to gate. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap` function is called by the router, so `msg.sender` inside the pool is the router contract. The pool passes this router address as `sender` to `_beforeSwap`, which forwards it verbatim to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. Any user can bypass a pool's swap allowlist by routing through the public router.

---

### Finding Description

`ExtensionCalling._beforeSwap` is called with `sender = msg.sender` of the pool's `swap` invocation: [1](#0-0) 

The pool dispatches `sender` directly into the extension call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*` or `exactOutput*`, the router is the direct caller of `pool.swap(...)`. Therefore `sender` arriving at `SwapAllowlistExtension.beforeSwap` is the **router address**, not the originating user. [3](#0-2) 

The extension's `allowedSwapper[pool][sender]` lookup resolves to `allowedSwapper[pool][router]`. If the router is allowlisted (a natural operational requirement so that legitimate users can trade), the gate is open to **every** user regardless of their individual allowlist status. If the router is not allowlisted, every router-mediated swap reverts even for individually allowlisted users.

The `DepositAllowlistExtension` avoids this class of error by checking `owner` (the LP-share recipient) rather than `sender` (the caller), making it robust to intermediary contracts: [4](#0-3) 

`SwapAllowlistExtension` has no equivalent indirection; it relies on `sender` being the end-user, which breaks when the router is in the call path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed. Any unprivileged user routes through `MetricOmmSimpleRouter`, the extension sees the allowlisted router address, and the swap proceeds. This constitutes an **admin-boundary break**: the pool admin's access control is circumvented by an unprivileged path (the public router). Depending on pool configuration, this can result in unauthorized price impact, unauthorized extraction of LP value, or violation of regulatory/operational constraints that the allowlist was designed to enforce — all constituting direct loss of protocol integrity and potentially LP principal.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly documented entry point for swaps. Any user aware of the router can exploit this without any special privilege. The pool admin has no on-chain mechanism to prevent router-mediated calls while keeping the router usable for allowlisted users. Likelihood is **high** for any pool that deploys `SwapAllowlistExtension` with the router in scope.

---

### Recommendation

Replace the `sender` check with the originating user identity. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation but is trustless if the extension validates the encoding.

2. **Check `recipient` instead of `sender`**: For exact-input swaps the recipient is the user; for exact-output it may differ. A cleaner fix is to mirror `DepositAllowlistExtension`'s approach — gate the economically relevant actor (the token recipient or the address that initiated the outermost call), not the intermediate dispatcher.

The extension interface already delivers both `sender` and `recipient` to `beforeSwap`: [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router-mediated swap to work.
3. Pool admin does **not** allowlist `attacker`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(sender=router, ...)`.
6. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Swap executes for `attacker` despite `allowedSwapper[pool][attacker] == false`.

The allowlist is fully bypassed. Any user with access to the public router can trade on a pool the admin intended to restrict. [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-43)
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
