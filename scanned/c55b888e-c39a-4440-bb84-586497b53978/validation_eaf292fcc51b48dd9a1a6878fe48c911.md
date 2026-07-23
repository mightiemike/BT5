### Title
`SwapAllowlistExtension` gates the router address instead of the actual user on router-mediated swaps, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is whatever the pool passes as the first argument — which is `msg.sender` of the pool's own `swap` call. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` with `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This is confirmed by the integration test in `FullMetricExtensionTest`, which allowlists `callers[0]` (the `TestCaller` contract that directly calls the pool), not `users[0]` (the EOA):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [2](#0-1) 

The `DepositAllowlistExtension` does not share this flaw because `addLiquidity` accepts `owner` as an explicit parameter — the liquidity adder can pass the user's address directly, and the extension checks that `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The swap path has no equivalent explicit-user parameter; the pool's `swap` call uses `msg.sender` as the sender identity forwarded to the extension.

---

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Outcome A — Broken allowlist (allowlisted users blocked):** The pool admin allowlists specific user addresses. Those users call the router. The extension checks the router address, which is not allowlisted, and reverts. Allowlisted users cannot use the primary public swap path; the pool's core swap functionality is broken for its intended audience.

**Outcome B — Full allowlist bypass (any user can swap):** To fix Outcome A, the admin allowlists the router address. Now `allowedSwapper[pool][router]` is `true`, so every call that arrives through the router passes the check regardless of who the actual user is. Any non-allowlisted address can bypass the restriction by calling `router.exactInputSingle()` with the restricted pool. The allowlist provides zero protection.

Outcome B is the direct-loss path: a pool configured to restrict swaps to KYC'd counterparties, specific market makers, or any curated set of addresses is fully open to arbitrary traders, violating the pool's intended access policy and potentially exposing LPs to trades they did not consent to.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entrypoint; most users interact through it rather than calling the pool directly.
- A pool admin who deploys `SwapAllowlistExtension` and allowlists individual users will immediately discover that router-based swaps fail (Outcome A). The natural remediation — allowlisting the router — produces Outcome B.
- No special privileges, flash loans, or unusual token behavior are required. Any EOA can call `router.exactInputSingle()`.

---

### Recommendation

The pool's `swap` function should accept an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The extension then checks that explicit identity rather than the pool's `msg.sender`. Alternatively, `SwapAllowlistExtension` can read the original user from a transient-storage context set by the router before the pool call, mirroring the existing callback-context pattern already used for reentrancy and swap settlement.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Alice calls `router.exactInputSingle({pool: pool, ...})`.
   - Router calls `pool.swap(...)` with `msg.sender = router`.
   - Extension checks `allowedSwapper[pool][router]` → `false` → **reverts**. Alice cannot swap.
4. Admin, to unblock Alice, calls `swapExtension.setAllowedToSwap(pool, router, true)`.
5. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
   - Router calls `pool.swap(...)` with `msg.sender = router`.
   - Extension checks `allowedSwapper[pool][router]` → `true` → **passes**.
   - Bob's swap executes in the supposedly restricted pool. Allowlist is fully bypassed. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
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
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L69-73)
```text
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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
