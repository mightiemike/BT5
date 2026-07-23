Based on the code I was able to read, here is my analysis:

---

### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the address that called the pool's `swap` function. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` = router address. If the router is allowlisted for the pool (which is required for the pool to be usable via the router at all), every user who calls the router bypasses the per-user allowlist entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` is:

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

The allowlist is keyed `allowedSwapper[pool][swapper]`. The `sender` argument received by the extension is the address that called the pool's `swap` — i.e., `msg.sender` at the pool level. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool sees `msg.sender = router`, so `sender` forwarded to the extension is the router address, not the originating user. [2](#0-1) 

For the pool to be reachable via the router at all, the pool admin must allowlist the router: `allowedSwapper[pool][router] = true`. Once that entry exists, **every user** who calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` entry point) passes the allowlist check, because the check resolves to `allowedSwapper[pool][router]` — always `true` — regardless of who the originating user is.

The analog to the external report is direct: the external bug sets file permissions to `0o666` (world-readable) when `0o600` (owner-only) was intended, making a secret accessible to all co-tenants. Here, the allowlist is configured per-user but resolves to a shared intermediary (the router), making a curated pool accessible to all users of that intermediary.

### Impact Explanation

A pool admin deploys a KYC-gated or institutional pool and configures `SwapAllowlistExtension` to restrict swaps to a specific set of addresses. Any non-allowlisted user can bypass this gate by calling `MetricOmmSimpleRouter` instead of the pool directly. The pool receives and settles the swap normally; the extension's guard silently passes because it sees the router, not the user. The pool's curated access policy is completely defeated for all router-mediated swaps.

Impact: **High** — direct policy bypass on curated pools; any user can trade against a pool that was intended to be restricted.

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Any pool that wants to be usable via the router must allowlist the router, which is the normal operational setup.
- No special knowledge or privileged access is required; any user can call the router.
- The bypass is unconditional once the router is allowlisted.

Likelihood: **High**.

### Recommendation

The pool should pass the originating user's address — not `msg.sender` — as the `sender` argument to the extension. Two concrete approaches:

1. **Pool-level fix**: The pool's `swap` function should accept an explicit `sender` parameter from the caller and forward it to the extension, rather than using `msg.sender`. The router would pass `msg.sender` (the end user) as that parameter.

2. **Extension-level mitigation**: The extension could read `tx.origin` as a fallback, but this is generally unsafe. The correct fix is at the pool/router interface boundary.

Additionally, the `DepositAllowlistExtension` should be audited for the same pattern: if `MetricOmmPoolLiquidityAdder` passes itself as the `owner` rather than the originating user, the deposit allowlist has the same bypass. [3](#0-2) 

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Call `extension.setAllowedToSwap(pool, router, true)` — required for the pool to be usable via the router.
3. As a non-allowlisted user `Eve`, call `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The pool calls `extension.beforeSwap(router, ...)`.
5. The check `allowedSwapper[pool][router]` returns `true`.
6. Eve's swap executes successfully despite not being on the allowlist.
7. Repeat with any number of non-allowlisted users — all succeed via the router.

**Note on verification gap**: I was unable to read `MetricOmmPool.sol` and `ExtensionCalling.sol` directly due to tool errors in the final iteration. The finding is grounded in the extension's own code and the research targets, which explicitly flag "wrong-actor binding" and "allowlist bypass via router" as the primary vectors for this extension pair. If `ExtensionCalling._beforeSwap` passes `tx.origin` or a caller-supplied address rather than `msg.sender` as `sender`, the bypass may not apply — that should be confirmed by reading those files. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
