### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the individual-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.addLiquidity` and `swap` both pass `msg.sender` as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

```solidity
// ExtensionCalling.sol line 95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

For `swap`, the pool similarly passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to every registered extension.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When a user calls through `MetricOmmSimpleRouter`, the call chain is:

```
User → MetricOmmSimpleRouter.exactInput*() → pool.swap()
```

The pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, **every user** — including those the admin explicitly never allowlisted — can swap on the curated pool simply by routing through `MetricOmmSimpleRouter`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and gates on `owner` (the economic beneficiary of the minted shares), which is caller-supplied and can be set to the real user even when an intermediary adder contract is used. The swap extension has no equivalent mechanism because the pool derives `sender` solely from `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: the admin intends to restrict trading to a specific set of addresses (e.g., KYC'd users, protocol partners, or whitelisted market makers). Once the router is allowlisted — a necessary step for any user to trade through the standard periphery — the allowlist is completely ineffective. Any unprivileged address can execute swaps against the pool's liquidity, draining LP value at oracle-derived prices without the LP's consent to serve that counterparty. This is a direct loss of LP principal and a broken core pool functionality (access-controlled trading).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly documented swap entry point. Any pool admin who deploys a curated pool and also wants users to use the router will allowlist the router. The bypass requires no special knowledge, no privileged keys, and no unusual transaction structure — any user simply calls the router's public `exactInput*` functions. The precondition (router allowlisted) is the normal operational state for any pool that uses both the allowlist extension and the router.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two approaches:

1. **Check `sender` only when it is not a trusted router; otherwise check a user identity forwarded by the router.** The router should forward the original `msg.sender` as `recipient` or via `extensionData`, and the extension should read that value.

2. **Gate on `recipient` instead of `sender` for swaps**, since `recipient` is the address that receives output tokens and is the economically relevant actor — analogous to how `DepositAllowlistExtension` gates on `owner` rather than `sender`.

3. **Alternatively**, remove router allowlisting and require users to call the pool directly for curated pools, but this breaks the standard UX.

The cleanest fix mirrors the deposit extension's design: pass the real user identity through a dedicated field that intermediaries are required to populate honestly, and verify it in the extension.

---

### Proof of Concept

**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` registered as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.

**Attack:**
```
attacker → MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, ...)
         → pool.swap(recipient=attacker, ...)   // msg.sender = router
         → _beforeSwap(sender=router, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
         → allowedSwapper[pool][router] == true  ✓  // passes!
```

**Result:** The attacker executes a swap on a pool they were never authorized to trade on. The allowlist invariant is broken: `allowedSwapper[pool][attacker] == false` yet the swap succeeds.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
