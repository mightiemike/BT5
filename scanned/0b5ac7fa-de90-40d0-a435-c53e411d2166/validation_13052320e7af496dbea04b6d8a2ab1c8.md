### Title
`SwapAllowlistExtension` Checks Router-Supplied `sender` Instead of End User, Allowing Any Unprivileged User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument it receives from the pool. That argument is the `msg.sender` of the pool's `swap` call. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Because the allowlist is keyed by `(pool, sender)`, the extension cannot distinguish between different end users going through the same router. If the router is allowlisted so that legitimate users can reach the pool, every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` reveals the shared pattern: the extension receives the pool's caller as its first `address` parameter and uses `msg.sender` (the pool) as the storage namespace key. [1](#0-0) 

By the same pattern, `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender /*pool*/][sender /*pool's msg.sender*/]`. The `sender` argument is whatever address called `pool.swap(...)`. When the call originates from `MetricOmmSimpleRouter`, that address is the router, not the end user. [2](#0-1) 

The pool's `swap` interface confirms that `msg.sender` is the entity that settles the input leg via the swap callback — i.e., the router when routing is used. [3](#0-2) 

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. When it calls `pool.swap(...)`, the pool and the extension both see `sender = router`. The allowlist check becomes `allowedSwapper[pool][router]` — a single boolean that either blocks all router users or permits all of them. [4](#0-3) 

The audit pivot for this path explicitly states the invariant that must hold:

> *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, designated market makers, or protocol-controlled bots). To allow those counterparties to use the standard router, the admin must allowlist the router address. Once the router is allowlisted, **any** address — including completely unauthorized users — can call `MetricOmmSimpleRouter` and have their swap accepted by the extension. The allowlist is rendered entirely ineffective for router-mediated swaps. Unauthorized users can drain LP inventory at oracle-quoted prices, extract fees, or execute trades the pool was designed to prevent.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, unprivileged contract. No special role, token balance, or prior interaction is required to call it. Any user who observes that the router is allowlisted on a curated pool can immediately exploit the bypass in a single transaction. The likelihood is **high**.

---

### Recommendation

The extension must gate the **original end user**, not the intermediate router. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the real user address into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to set the field honestly, which is acceptable since the router is a known periphery contract.

2. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; instead, allowlisted users must call `pool.swap(...)` directly. The extension's NatSpec should state this constraint explicitly.

Either way, the extension must never treat the router's address as the identity to gate.

---

### Proof of Concept

```
1. Admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can reach the pool via the router.
3. Unauthorized user (not in allowedSwapper) calls:
     MetricOmmSimpleRouter.exactInput(pool, zeroForOne=true, amountIn, ...)
4. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, cbData, extData).
   pool's msg.sender = router.
5. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
   Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. Swap executes. Unauthorized user receives token output.
   The allowlist has been fully bypassed.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` keying on `sender` (the pool's immediate caller) rather than the originating end user, combined with the fact that `MetricOmmSimpleRouter` is a public contract that any address can invoke. [2](#0-1) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-30)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {Address} from "@openzeppelin/contracts/utils/Address.sol";
import {MetricOmmSwapRouterBase} from "./base/MetricOmmSwapRouterBase.sol";
import {PeripheryPayments} from "./base/PeripheryPayments.sol";
import {SelfPermit} from "./base/SelfPermit.sol";
import {IMetricOmmSimpleRouter} from "./interfaces/IMetricOmmSimpleRouter.sol";
import {IMulticall} from "./interfaces/IMulticall.sol";
import {MetricOmmSwapPath} from "./libraries/MetricOmmSwapPath.sol";
import {MetricOmmSwapInputs} from "./libraries/MetricOmmSwapInputs.sol";
import {MetricOmmSwapResults} from "./libraries/MetricOmmSwapResults.sol";

/// @title MetricOmmSimpleRouter
/// @notice Exact-input and exact-output swaps through one or more MetricOmm pools.
/// @dev Expected callback pool, payer, token, and swap mode are stored in transient storage at entry.

contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
  /// @notice Transient callback mode is not supported by this router.
  /// @param callbackMode Unrecognized mode read from transient storage.
  error InvalidCallbackMode(uint8 callbackMode);

  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}

  // ============ Types ============

  struct ExactOutputIterateCallbackData {
    address[] tokens;
    address[] pools;
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

**File:** generate_scanned_questions.py (L732-738)
```python
        Vector(
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
