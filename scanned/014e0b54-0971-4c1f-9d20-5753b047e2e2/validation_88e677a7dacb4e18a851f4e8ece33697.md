### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. This creates an irreconcilable asymmetry: either the router is allowlisted (opening the curated pool to every user who routes through it) or it is not (blocking all allowlisted users from using the router). Either path breaks the intended per-user gating invariant.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is keyed on the `sender` argument: [1](#0-0) 

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

`msg.sender` here is the pool (correct — it is the pool calling the extension). `sender` is the first argument the pool forwards, which is `msg.sender` of the originating `pool.swap()` call — i.e., the direct caller of the pool. When `MetricOmmSimpleRouter` calls `pool.swap(recipient = user, ...)`, the pool's `msg.sender` is the router, so the extension receives `sender = router`, not `sender = actualUser`.

This is confirmed by the integration test, which allowlists `callers[0]` (the direct-calling contract) rather than `users[0]` (the economic beneficiary/recipient): [2](#0-1) 

The pool's own error documentation acknowledges that `swap` checks `msg.sender`: [3](#0-2) 

The allowlist admin mappings are keyed `allowedSwapper[pool][swapper]`: [4](#0-3) 

When the router is the direct caller, the lookup becomes `allowedSwapper[pool][router]`. The pool admin faces two bad choices:

1. **Allowlist the router** → every user (including disallowed ones) can bypass the per-user gate by routing through the router.
2. **Do not allowlist the router** → individually allowlisted users cannot use the router at all; only direct `pool.swap()` calls work.

Neither option preserves the intended per-user curation policy.

---

### Impact Explanation

**High — direct policy bypass enabling unauthorized swaps in curated pools.**

A curated pool (e.g., KYC-gated, institution-only, or whitelist-restricted) relies on `SwapAllowlistExtension` to prevent unauthorized users from trading. If the router is allowlisted (the only way to support router-mediated swaps), any unprivileged user can call `MetricOmmSimpleRouter.exactInput/exactOutput` and swap in the pool without being individually allowlisted. This exposes LP funds to unauthorized counterparties and defeats the entire purpose of the curation mechanism. The loss is direct: unauthorized swappers extract value from LP positions at oracle-derived prices.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, which immediately opens the gate to all users. The attacker needs no special privileges — only the ability to call the public router. The condition (router allowlisted on a curated pool) is the normal operational state for any production curated pool that intends to be usable.

---

### Recommendation

The `beforeSwap` hook must receive and check the **economic actor** (the end user), not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes the actual user address in `extensionData`; the extension decodes and checks it. This requires the router to cooperate and the extension to trust the encoding — fragile.

2. **Preferred — check `recipient` instead of `sender`**: The pool already passes `recipient` as the second argument to `beforeSwap`. For swap allowlists, the economically relevant actor is the recipient of output tokens. Change the hook to check `recipient`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

3. **Alternative — router forwards caller identity**: The router passes `msg.sender` (the actual user) as `sender` to the pool, and the pool forwards it to the extension. This requires a pool-level change to accept a `sender` override from trusted periphery contracts.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only address(alice) for swapping
  - Pool admin also allowlists address(router) so router-mediated swaps work
  - Pool admin does NOT allowlist address(bob)

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
  2. Router calls pool.swap(recipient=bob, ...)
  3. Pool calls extension.beforeSwap(sender=router, recipient=bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — Bob receives output tokens despite not being allowlisted

Result:
  - Bob bypasses the per-user swap allowlist
  - Bob can trade in a KYC/curated pool without authorization
  - LP funds are exposed to an unauthorized counterparty
  - Alice's individual allowlist entry is irrelevant — any user can route through the router
``` [1](#0-0) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
