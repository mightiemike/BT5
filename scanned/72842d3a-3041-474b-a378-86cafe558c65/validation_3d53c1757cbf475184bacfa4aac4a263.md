Based on the code I was able to read, here is my analysis:

---

### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender`, so the extension checks the router's address rather than the end user's address. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the individual allowlist by routing through the same public router contract.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool and `sender` is whatever the pool passes — which is `msg.sender` inside the pool (the immediate caller). The `FullMetricExtensionTest` confirms this: the test allowlists `address(callers[0])` (the `TestCaller` wrapper contract), not `users[0]` (the human address), because the pool passes the immediate caller as `sender`. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity`, by contrast, ignores the first (`sender`) parameter and checks `owner` — the LP position owner — which correctly identifies the economic beneficiary regardless of who the immediate caller is. [3](#0-2) 

This creates a structural asymmetry: deposit guards check the right actor; the swap guard checks the wrong one for router-mediated flows.

### Impact Explanation

A pool admin who wants to restrict swaps to a curated set of users faces an impossible configuration dilemma:

- **Do not allowlist the router** → all router-mediated swaps revert, even for permitted users. The router is unusable on this pool.
- **Allowlist the router** → the extension now passes for any `sender = router`, regardless of who called the router. Every non-permitted user can bypass the individual allowlist by calling `MetricOmmSimpleRouter.exactInput/exactOutput`, which is a public, permissionless contract.

There is no configuration that simultaneously allows permitted users to swap through the router and blocks non-permitted users from doing the same. The allowlist is therefore unenforceable on any pool that supports router-mediated swaps, which is the primary intended usage path.

Consequence: unauthorized users trade in a curated pool, extracting fees, causing price impact, or violating regulatory/compliance restrictions the pool admin intended to enforce. This is a direct policy bypass with fund-level consequences (unauthorized fee extraction, unauthorized price impact on LP positions).

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint in the periphery layer.
- Any pool that configures `SwapAllowlistExtension` and also wants to support router swaps (the common case) must allowlist the router, triggering the bypass.
- The attacker needs no special privileges: they call the public router with a valid swap, and the extension passes because the router is allowlisted.
- The `generate_scanned_questions.py` research notes explicitly flag this: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

### Recommendation

Pass the original end-user address through the call chain so the extension can check it. Two approaches:

1. **Pool-level fix**: Have `pool.swap()` accept an explicit `sender` parameter (the end user) rather than using `msg.sender`, and have the router forward `msg.sender` (the actual user) as that parameter. The extension then checks the true originator.

2. **Extension-level fix**: Mirror the deposit allowlist design — ignore the `sender` parameter and instead check a user-supplied identity from `extensionData`, which the router populates with `msg.sender` before forwarding.

Either way, the invariant must be: *the identity checked by `SwapAllowlistExtension` is the address that economically benefits from the swap*, not the intermediate contract that relays it.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `extension2` and `beforeSwap` order set to `2`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(...)` → pool passes `sender = address(router)` to the extension → extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
6. Attacker has swapped on a pool they were explicitly not permitted to access.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the LP beneficiary) rather than the immediate caller, confirming the swap extension's actor binding is the root cause. [1](#0-0) [3](#0-2)

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

**File:** generate_scanned_questions.py (L659-663)
```python
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
