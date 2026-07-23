### Title
Swap Allowlist Bypassed via Router Intermediary: Any Non-Allowlisted User Can Swap on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the **direct caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`. If the router address is allowlisted (a natural operational choice so that users can use the supported periphery), every non-allowlisted user can bypass the curated pool's swap gate by simply routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

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

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded by the pool — which is the **direct caller of `pool.swap()`**, i.e., `MetricOmmSimpleRouter` when a user routes through it. [2](#0-1) 

The allowlist is keyed as `allowedSwapper[pool][swapper]`. When a pool admin allowlists the router address (the natural operational setup so that users can use the supported periphery), the check becomes:

```
allowedSwapper[pool][router] == true  →  passes for every user who calls the router
```

Any non-allowlisted EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle()` (or any multi-hop variant), which calls `pool.swap()` with `msg.sender = router`. The extension sees `sender = router`, finds it allowlisted, and allows the swap — completely bypassing the per-user curation the pool admin intended.

The audit pivot for this path explicitly flags this concern: [3](#0-2) 

The `FullMetricExtensionTest` confirms the pattern: the test allowlists `address(callers[0])` (the `TestCaller` wrapper contract) as the swapper, not `users[0]` (the underlying EOA), showing that the pool always passes the direct caller — not the originating user — as `sender`. [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). Any non-allowlisted user bypasses this gate by routing through `MetricOmmSimpleRouter`. The pool's liquidity is exposed to unrestricted public trading, defeating the curation entirely. LP funds are at risk from uninvited counterparties, and any fee or risk model predicated on a closed participant set is broken.

**Severity: High** — direct bypass of a configured access-control guard with fund-impacting consequences (LP exposure to unintended counterparties, potential for adversarial MEV or price manipulation by actors the pool was designed to exclude).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported swap entrypoint; pool admins are expected to allowlist it.
- No privileged access is required — any EOA can call the router.
- The bypass requires only a single standard router call; no flash loans, callbacks, or special setup needed.
- The `SwapAllowlistExtension` is a production periphery contract, not a mock. [5](#0-4) 

---

### Recommendation

The `beforeSwap` hook must gate the **economically relevant actor** — the originating user — not the intermediary. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the true `tx.origin` or a signed user identity in `extensionData`, and the extension verifies it. This requires trust in the router not to forge identities.

2. **Check `tx.origin` instead of `sender`**: Replace `sender` with `tx.origin` in the allowlist lookup. This correctly identifies the EOA initiating the transaction regardless of routing depth, though it is incompatible with smart-contract swappers.

3. **Do not allowlist the router; require direct pool calls for curated pools**: Document that curated pools must be accessed directly. This is operationally restrictive but closes the bypass.

The cleanest production fix is option 1 with a router that appends a verified caller identity to `extensionData`, which the extension decodes and checks against the allowlist.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker (non-allowlisted EOA) calls:
       MetricOmmSimpleRouter.exactInputSingle(
           pool, zeroForOne=true, amountIn=X, priceLimit=..., extensionData=...
       )
  2. Router calls pool.swap(sender=router, ...)
  3. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  →  passes
  5. Swap executes for attacker despite not being on the allowlist

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool
  - SwapAllowlistExtension guard is completely bypassed via the router intermediary
  - Identical to the external report's approve+transferFrom pattern:
    the intermediary (router / transferFrom) is authorized, so the
    restriction on the end-user is silently voided
``` [1](#0-0) [6](#0-5)

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
