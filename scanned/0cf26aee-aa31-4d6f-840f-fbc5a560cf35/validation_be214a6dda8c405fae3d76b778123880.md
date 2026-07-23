### Title
`SwapAllowlistExtension` gates the router address instead of the actual user on router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap` call — the direct caller. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (which an admin must do to let any user reach the pool through the router), every unprivileged user bypasses the per-user swap allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument the pool forwards from its own `swap` call. The pool derives `sender` from its own `msg.sender` — the direct caller.

The test suite confirms this binding. In `FullMetricExtension.t.sol`, the allowlist is set for `address(callers[0])` — the `TestCaller` contract that directly calls the pool — not for `users[0]` (the end-user address passed as `recipient`):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [2](#0-1) 

`callers[0]` is the direct caller of the pool; `users[0]` is only the recipient. This confirms `sender = msg.sender` of `pool.swap`.

When a user routes through `MetricOmmSimpleRouter`:

1. User calls `router.exactInput(...)`.
2. Router calls `pool.swap(...)` — pool's `msg.sender` = router.
3. Pool calls `extension.beforeSwap(sender = router, ...)`.
4. Extension evaluates `allowedSwapper[pool][router]`.

The individual user's address is never checked. The allowlist is keyed on the router, not the human swapper.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the second argument, explicitly passed by the pool from the `addLiquidity` call parameters), which the `MetricOmmPoolLiquidityAdder` sets to the caller-supplied `owner` address — the economically relevant actor. [3](#0-2) 

The asymmetry is structural: deposit allowlists gate `owner` (explicit parameter), swap allowlists gate `sender` (derived from `msg.sender`).

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists `MetricOmmSimpleRouter` (the only way to let any user reach the pool through the supported periphery) inadvertently opens the pool to every unprivileged user. Any address — including those the admin explicitly excluded — can call `router.exactInput` or `router.exactOutput`, have the router call `pool.swap`, and pass the allowlist check because the extension sees the allowlisted router, not the blocked user.

The consequence is that the curated pool's swap access control is completely nullified for router-mediated paths: non-allowlisted users can execute swaps, receive token output, and interact with pool liquidity in ways the admin intended to prevent. This constitutes an admin-boundary break where an unprivileged path bypasses a configured guard with direct fund-flow consequences (unauthorized swap settlement against pool reserves).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint. Any admin who wants allowlisted users to be able to use the router must allowlist the router itself, because the extension only sees the router as `sender`. This is the natural, expected configuration — the admin has no other option if they want router access at all. The bypass is therefore triggered by the normal, intended administrative setup, not by an exotic configuration.

---

### Recommendation

Pass the original user address as an explicit `sender` parameter through the pool's `swap` function rather than using `msg.sender`. The pool should accept `sender` as a caller-supplied argument (as it already does for `owner` in `addLiquidity`), and the router should forward `msg.sender` (the actual user) as that argument. The extension then checks the human swapper, not the intermediary.

Alternatively, the `SwapAllowlistExtension` can be redesigned to check `msg.sender` of the extension call (the pool) against a registry that maps pool → router → allowed-user set, but the simpler fix is to thread the real user address through the call stack.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — the only way to allow router-mediated swaps.
3. Admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `router.exactInput({tokenIn, tokenOut, pool, amountIn, ...})`.
5. Router calls `pool.swap(recipient=attacker, ...)`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Attacker receives swap output despite never being allowlisted.

The per-user allowlist is fully bypassed for every user who routes through the allowlisted router.

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
