### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the pool's `msg.sender` — which is the `MetricOmmSimpleRouter` contract, not the originating end-user. A pool admin who allowlists the router to enable standard swap flows inadvertently opens the gate to every user who can call the router, defeating the curated-pool access policy entirely.

---

### Finding Description

`MetricOmmPool.swap` is invoked by `MetricOmmSimpleRouter` on behalf of end-users. The pool passes its own `msg.sender` (the router) as the `sender` argument to every configured extension hook, including `SwapAllowlistExtension.beforeSwap`. The extension then performs an allowlist lookup keyed by `(pool, sender)`:

```
allowAllSwappers[msg.sender] || allowedSwapper[msg.sender][sender]
```

where `msg.sender` is the pool and `sender` is the router address. [1](#0-0) 

Because `MetricOmmSimpleRouter` is a permissionless public contract with no per-user access control, allowlisting it is equivalent to allowlisting every user on the internet. The extension has no way to recover the original end-user from the hook arguments.

The asymmetry with the deposit side makes this concrete: `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, not the payer), so the deposit allowlist gates the economically relevant actor. [2](#0-1) 

The swap allowlist has no equivalent correct binding — it checks the intermediary contract, not the economic actor. This is confirmed by the integration test, which must allowlist `callers[0]` (the `TestCaller` intermediary contract) rather than `users[0]` (the actual user) for swaps to succeed: [3](#0-2) 

The `generate_scanned_questions.py` audit pivot explicitly flags this: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

---

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The pool admin's intent to restrict trading to specific counterparties is completely nullified. Unauthorized users can execute swaps on pools designed for restricted access, breaking the core access-control invariant. This is an admin-boundary break: an unprivileged path (the public router) bypasses a factory-registered extension guard that the pool admin configured to control who may trade.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard, documented swap entry point. A pool admin who configures `SwapAllowlistExtension` and then allowlists the router — the natural step to enable standard swap flows for permitted users — unknowingly opens the pool to all users. The bypass requires no special privileges, no flash loans, and no complex setup: just a standard router call from any EOA.

---

### Recommendation

The `SwapAllowlistExtension` must check the original end-user, not the router. Concrete options:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` pass the original caller's address in `extensionData`; have the extension decode and verify it (requires trusting the router as an authorized forwarder, enforced via a separate router allowlist in the extension).
2. **Core hook signature change**: Add the original caller as a distinct argument in the `beforeSwap` hook signature so the pool can pass it explicitly.
3. **Documentation + direct-call requirement**: Document that curated pools must allowlist individual user addresses and that those users must call the pool directly — not through the router. This breaks the standard UX but preserves correctness.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a configured extension.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — intending to enable the standard swap path for permitted users.
3. Attacker (a non-allowlisted EOA) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `SwapAllowlistExtension.beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` = `true` → passes.
7. Swap executes successfully. The attacker has traded on a pool they were never meant to access, with zero special setup beyond a standard router call. [5](#0-4)

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
