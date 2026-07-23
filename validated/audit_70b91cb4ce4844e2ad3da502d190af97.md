Based on my analysis of the codebase, I can identify a valid analog vulnerability. Let me present the finding.

---

### Title
SwapAllowlistExtension gates the router address instead of the actual end-user swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-pool allowlist by checking the `sender` argument passed to it by the pool. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the pool forwards the router's address as `sender` to the extension. If the pool admin allowlists the router (which is required for any allowlisted user to use the router), every non-allowlisted user can bypass the restriction by routing through the same public contract.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the first argument the pool passes when it calls the extension hook. The pool passes the direct caller of `pool.swap()` as `sender`. When `MetricOmmSimpleRouter` mediates a swap it calls `pool.swap()` itself, so the extension receives the router's address as `sender`, not the address of the end user.

The `recipient` parameter — which does carry the actual user's address in a router-mediated flow — is the second argument to `beforeSwap` and is silently discarded (unnamed `address` parameter):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

For allowlisted users to be able to use the router at all, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, every user — allowlisted or not — can call `MetricOmmSimpleRouter.exactInput` / `exactOutput`, have the router call `pool.swap(sender=router, ...)`, and pass the extension check because the extension sees the router (allowlisted) rather than the actual user (not allowlisted).

The `DepositAllowlistExtension` has the symmetric design but checks `owner` (the second parameter) rather than `sender` (the first, unnamed parameter):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Whether `MetricOmmPoolLiquidityAdder` passes the actual user or itself as `owner` determines whether the deposit path has the same flaw; the swap path is the clearest reachable root cause.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses). The bypass allows any unprivileged user to execute swaps on such a pool by routing through the public `MetricOmmSimpleRouter`. This constitutes a broken core pool functionality — the allowlist enforcement — and can result in direct loss of LP principal if the pool's liquidity is priced for a restricted counterparty set (e.g., tighter spreads, no adverse-selection protection).

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, supported periphery contract. The bypass requires only two conditions, both of which are operationally necessary:

1. The pool has `SwapAllowlistExtension` configured with at least one allowlisted user.
2. The pool admin has allowlisted the router so that allowlisted users can use it.

Once condition 2 is met, the bypass is trivially reachable by any address with no special privileges.

### Recommendation

The extension must gate the economically relevant actor, not the direct pool caller. Concrete options:

1. **Check `recipient` instead of `sender`** in `beforeSwap` — the router passes the actual user as `recipient`.
2. **Have the router pass the actual user as `sender`** to `pool.swap()` so the extension sees the correct identity.
3. **Use `extensionData`** to carry a signed user identity that the extension verifies, decoupling the allowlist check from the call-stack actor.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)      // required for userA to use router
4. Non-allowlisted userB calls MetricOmmSimpleRouter.exactInput(pool, ...)
5. Router calls pool.swap(sender=router, recipient=userB, ...)
6. Pool calls extension.beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true  →  passes
8. userB's swap executes on the restricted pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
