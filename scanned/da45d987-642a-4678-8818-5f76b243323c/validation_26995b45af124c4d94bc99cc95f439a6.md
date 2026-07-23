Based on my investigation of the repository, I can identify a valid analog vulnerability.

---

### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (required for any user to swap through it), every unprivileged user bypasses the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is whatever the pool passes as the first argument to the hook — confirmed by the test suite to be `msg.sender` of `pool.swap()`, i.e., the immediate caller of the pool:

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L69-73
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true); // gates TestCaller, not users[0]
_addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
_swap(0, users[0], false, int128(1000), type(uint128).max);
```

The test allowlists `address(callers[0])` — a per-user `TestCaller` contract — not `users[0]` (the EOA). This confirms the pool passes `msg.sender` (the intermediate caller) as `sender` to the extension.

In production, `MetricOmmSimpleRouter` is a **shared** contract used by all users. When any user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap()`, making the router the `msg.sender`. The extension then evaluates `allowedSwapper[pool][router_address]`.

This creates an irresolvable dilemma for pool admins:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router — broken core functionality |
| Router **allowlisted** | **All** users bypass the allowlist via the router — full policy bypass |

A pool admin who intends to gate individual users (e.g., KYC-gated or institutional pools) and also wants those users to access the standard periphery must allowlist the router. Doing so opens the gate to every unprivileged user.

---

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension`-protected pool by routing through `MetricOmmSimpleRouter` once the router is allowlisted. The allowlist — the sole access-control mechanism on curated pools — is rendered ineffective. Non-allowlisted users can trade on pools explicitly configured to exclude them, causing direct loss of curation policy and potentially enabling unauthorized extraction of LP value from restricted pools.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the standard, documented swap entry point. Any user aware that the router is allowlisted (observable on-chain via `allowedSwapper[pool][router]`) can immediately exploit the bypass with a normal router call. No privileged access, special tokens, or malicious setup is required.

---

### Recommendation

The pool should pass the **originating user's address** — not `msg.sender` — as `sender` to extension hooks, or the router should implement a trusted-forwarder pattern that appends the real user's address to calldata. Alternatively, `SwapAllowlistExtension` should check the `recipient` field (if it reliably identifies the economic actor) or require that the pool expose a separate `originSender` parameter distinct from `msg.sender`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow allowlisted users to use the router.
3. Non-allowlisted EOA `attacker` calls `MetricOmmSimpleRouter.exactInput(pool, ...)`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = `router`.
5. Pool calls `extension.beforeSwap(router, recipient, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Swap executes. `attacker` was never individually allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L32-38)
```text
  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
