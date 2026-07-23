### Title
SwapAllowlistExtension gates on `sender` (immediate pool caller) rather than the actual user, making the allowlist either broken or bypassable through MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-user allowlist against the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension sees the router address as the swapper — not the actual end-user. This creates an irreconcilable split: either the router is not allowlisted (allowlisted users cannot use the router at all) or the router is allowlisted (every user, regardless of individual allowlist status, can bypass the guard through the router).

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

The pool's `ExtensionCalling._beforeSwap` passes `sender` (the pool's own `msg.sender`, i.e. the immediate caller of `pool.swap()`) as the first argument to every extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(recipient=user, ...)`, the pool records `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The `DepositAllowlistExtension` avoids this problem for deposits by checking `owner` (the position beneficiary, second argument), not `sender` (the payer/caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The swap extension has no equivalent fallback to the actual user identity. The existing integration test confirms the design: the test allowlists `callers[0]` (the `TestCaller` contract that calls the pool), not `users[0]` (the human recipient), which is exactly the wrong actor when a router intermediary is present. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a known set of addresses faces two bad outcomes:

1. **Router not allowlisted**: Individually allowlisted users who call through `MetricOmmSimpleRouter` are rejected (`NotAllowedToSwap`). The supported periphery path is unusable for the curated pool, breaking core swap functionality for legitimate users.

2. **Router allowlisted** (the natural fix an admin would attempt): `allowedSwapper[pool][router] = true` passes the check for every caller of the router, regardless of whether the actual end-user is on the allowlist. Any address can bypass the per-user curation policy by routing through the router. The allowlist is rendered entirely ineffective for router-based swaps, which is the primary user-facing swap path.

In the second scenario, a non-allowlisted address can execute swaps in a pool that was explicitly configured to deny them, constituting a direct admin-boundary break and curation failure.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery extension explicitly designed for curated pools.
- `MetricOmmSimpleRouter` is the primary user-facing swap entry point.
- A pool admin who wants router-based swaps to work will naturally attempt to allowlist the router, triggering the bypass.
- No privileged attacker capability is required beyond calling the public router.
- The asymmetry with `DepositAllowlistExtension` (which correctly checks `owner`) makes the swap extension's behavior non-obvious and likely to be misconfigured.

---

### Recommendation

The `beforeSwap` hook receives both `sender` (immediate caller) and `recipient` (output beneficiary). Neither alone is the correct identity for all call paths. The recommended fix is to have the router forward the actual user's address through `extensionData`, and have `SwapAllowlistExtension` decode and check that address when present, falling back to `sender` for direct pool calls. Alternatively, document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by rejecting pools that configure both.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Call `swapExtension.setAllowedToSwap(pool, router, true)` — the admin allowlists the router so that router-based swaps work.
3. From an address `attacker` that is **not** in the allowlist, call `router.swap(pool, ...)`.
4. The pool calls `extension.beforeSwap(sender=router, ...)`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
6. The swap executes for `attacker` despite `allowedSwapper[pool][attacker]` being `false`.

The allowlist is bypassed with zero privileged access; the only requirement is using the public router path instead of calling `pool.swap()` directly. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
