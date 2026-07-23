Based on my investigation of the codebase, I found a genuine analog to the external bug class. The external report is about a loop-based DoS preventing refunds; the Metric OMM analog is a **wrong-actor binding** in the `SwapAllowlistExtension` that allows any user to bypass a curated pool's allowlist by routing through `MetricOmmSimpleRouter`.

---

### Title
SwapAllowlistExtension Checks Direct Caller Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument (the first parameter the pool passes to the extension hook). Test evidence in `FullMetricExtension.t.sol` confirms the pool forwards the **direct caller's address** — not the end-user's address — as `sender`. When any user swaps through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension sees the router's address, not the user's. A pool admin who whitelists the router to enable periphery access inadvertently opens the allowlist to every user on the network.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

`msg.sender` is the pool (correct), and `sender` is the first argument the pool supplies. The allowlist lookup is `allowedSwapper[pool][sender]`.

The test in `FullMetricExtension.t.sol` reveals the binding: [2](#0-1) 

The allowlist entry is set for `callers[0]` (the `TestCaller` wrapper contract), **not** for `users[0]` (the actual human user). The swap only succeeds because the pool passes the direct caller's address — `callers[0]` — as `sender` to the extension. `users[0]`'s address is never checked.

When a real user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router becomes the direct caller of `pool.swap(...)`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Two exploitable outcomes follow:

1. **Allowlist broken for legitimate users**: A pool admin whitelists specific user addresses. Those users cannot swap through the router because the router's address is not whitelisted. Core swap functionality is broken for the intended audience.

2. **Full allowlist bypass**: To restore router access, the pool admin whitelists the router address. This single entry grants every address on the network the ability to swap in the curated pool, completely defeating the curation policy.

The `DepositAllowlistExtension` has a structurally identical binding for `beforeAddLiquidity`, confirmed by the same test pattern: [3](#0-2) [4](#0-3) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` (e.g., a KYC-gated or institution-only pool) cannot enforce its allowlist when users interact through the canonical `MetricOmmSimpleRouter`. Any unprivileged user can bypass the guard by routing through the router if the router address is whitelisted, or the pool admin is forced into a choice between broken router access and no curation at all. This constitutes a **broken core pool functionality** and a **curation failure** with direct fund-impact consequences (unauthorized users can trade at oracle-anchored prices in a pool not designed for them).

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported public entrypoint for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to use the router will encounter this issue. The trigger requires no special privilege — any user can call the router. The pool admin action (whitelisting the router) is a natural and expected remediation attempt that makes the bypass reachable.

### Recommendation

The pool should forward the **actual user's address** — not `msg.sender` — as the `sender` argument to extension hooks. Concretely:

- `pool.swap()` should accept an explicit `payer` or `user` parameter that the caller (router) populates with `msg.sender` at the router level, and the pool should pass that value to `extension.beforeSwap(user, ...)`.
- Alternatively, `SwapAllowlistExtension.beforeSwap` should accept and check a second identity field (e.g., `recipient`) that the router populates with the actual user address, rather than relying solely on the first `sender` argument.

The invariant to enforce: **every guard must key authorization to the same actor that the economic action is actually attributed to**, regardless of which supported public entrypoint reaches the pool.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — intending to allow only Alice.
3. Bob (not whitelisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(...)` — the pool passes the router's address as `sender` to the extension.
5. The extension evaluates `allowedSwapper[pool][router]` — false, swap reverts. Alice also cannot use the router.
6. Pool admin, to restore router access, calls `swapExtension.setAllowedToSwap(pool, router, true)`.
7. Bob calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` again — extension evaluates `allowedSwapper[pool][router]` — true, swap succeeds. Bob has bypassed the allowlist. [5](#0-4) [2](#0-1)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L34-41)
```text
  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
