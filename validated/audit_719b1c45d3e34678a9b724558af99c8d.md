Based on my investigation of the codebase, I can identify a concrete analog to the voting-power guard bypass.

---

### Title
`SwapAllowlistExtension` Gates on Direct Pool Caller (`sender = msg.sender`) Instead of the Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` to the pool (the direct caller). When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension gates on the **router address** rather than the **actual user**. If the router is allowlisted (or `allowAllSwappers` is set for the pool), any unprivileged user can bypass the curated-pool swap gate entirely by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the production guard for curated pools that restrict who may swap:

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

Here `msg.sender` is the pool (correct — enforced by `onlyPool` in `BaseMetricExtension`), and `sender` is the first argument the pool passes through `ExtensionCalling._beforeSwap`:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
``` [2](#0-1) 

The pool derives `sender` from `msg.sender` at the pool boundary (the direct caller). The `FullMetricExtensionTest` confirms this: the test allowlists `callers[0]` — the `TestCaller` contract that directly calls the pool — not `users[0]` (the human behind it):

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter` (the public multi-hop router), the router is `msg.sender` to the pool, so `sender` in the extension becomes the **router address**. The allowlist then checks whether the router is allowlisted, not whether the actual user is allowlisted.

### Impact Explanation

Two fund-impacting scenarios arise:

1. **Allowlist bypass (High)**: If the pool admin allowlists the router (a natural operational step to let users use the standard periphery), every unprivileged user can bypass the curated-pool swap gate by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against unauthorized swappers on the curated pool.

2. **Allowlisted users locked out (Medium)**: If the router is not allowlisted, users who are individually allowlisted cannot use the router at all — their swaps revert with `NotAllowedToSwap` even though they are authorized. Core swap functionality is broken for the intended user set.

Both outcomes break the invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it. [4](#0-3) 

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint described in the protocol documentation. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the standard router is immediately affected. The bypass requires no special privileges — any user can call the public router. [1](#0-0) 

### Recommendation

The extension must gate on the **economically relevant actor** — the human or contract that initiated the swap — not the intermediate router. Two approaches:

1. **Check `recipient` or a user-supplied identity field** if the pool's `swap` function accepts an explicit `sender` parameter that the router populates with `msg.sender` (the actual user).
2. **Require direct pool calls only** for allowlisted pools (document that the router must not be used with `SwapAllowlistExtension`), or add a router-level forwarding mechanism that passes the originating user as `sender`.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the explicit LP-position owner parameter), not `sender` (the direct caller):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

`SwapAllowlistExtension` should adopt the same pattern — gate on the actor the pool intends to restrict, not the intermediary.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, routerAddress, true)` to allow the standard router (or `setAllowAllSwappers(pool, false)` with only specific users allowlisted).
3. Non-allowlisted user `Alice` calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. The router calls `pool.swap(...)` — the pool sets `sender = address(router)`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Alice, who is not in the allowlist, successfully swaps on the curated pool, violating the curation invariant. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L69-73)
```text
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
