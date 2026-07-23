### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. Evidence from the integration test suite shows the pool forwards `msg.sender` of the `pool.swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the **router contract**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads the first argument (`sender`) and checks it against the per-pool allowlist:

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

The integration test confirms the pool forwards `msg.sender` of the `pool.swap()` call as `sender`:

```solidity
// test allowlists callers[0] (a TestCaller contract that is the direct pool caller)
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
// swap succeeds because callers[0] == msg.sender seen by the pool
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
user → MetricOmmSimpleRouter.exactInput(...)
           → pool.swap(...)          // msg.sender = router
               → extension.beforeSwap(sender = router, ...)
```

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. But `MetricOmmSimpleRouter` is a public, permissionless contract — allowlisting it grants every user the ability to swap, regardless of whether they are individually allowlisted.

Contrast this with `DepositAllowlistExtension`, which correctly checks the `owner` parameter (the position owner, not the adder contract), so the deposit gate is not broken by the `MetricOmmPoolLiquidityAdder`:

```solidity
// DepositAllowlistExtension checks owner (the LP), not msg.sender (the adder)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The swap extension has no equivalent mechanism to recover the true end-user identity from a router-mediated call.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC/compliance or curated-access purposes loses its enforcement guarantee the moment the router is allowlisted. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInput` (or any `exact*` variant) and execute swaps on the restricted pool. This is a direct policy bypass with fund-impacting consequences: non-permitted actors can drain liquidity from a pool that was intended to be restricted, and LP providers who deposited under the assumption of a curated counterparty set are exposed to unrestricted swap flow.

---

### Likelihood Explanation

The scenario is highly reachable. Pool admins who deploy a swap allowlist almost certainly also want their allowlisted users to be able to use the standard router (the primary UX entry point). Allowlisting the router is the natural, expected configuration step. The RESEARCHER.md explicitly identifies this path as a priority audit target ("swap allowlist gate … whether router-mediated swaps preserve that identity"). [4](#0-3) 

---

### Recommendation

Pass the end-user identity through the swap hook rather than the direct caller. Two concrete approaches:

1. **Encode the true sender in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router, so it must also verify `msg.sender` (the pool's direct caller) is a known, trusted periphery contract.

2. **Add a `trustedForwarder` registry to `SwapAllowlistExtension`**: Pool admins register approved forwarder contracts (e.g., the router). When `msg.sender` is a registered forwarder, the extension reads the true sender from a standardized field in `extensionData` instead of from the `sender` argument.

Either approach mirrors how `DepositAllowlistExtension` correctly gates the `owner` (end user) rather than the adder contract.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Direct swap by bob (non-allowlisted) → reverts correctly
vm.prank(bob);
pool.swap(bob, recipient, false, 1000, type(uint128).max, "");
// → NotAllowedToSwap ✓

// Router-mediated swap by bob → succeeds (bypass)
// Pool admin allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimit: type(uint128).max,
    recipient: bob,
    extensionData: ""
}));
// → succeeds; bob swapped on a pool he was not individually allowlisted for
``` [1](#0-0) [5](#0-4)

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
