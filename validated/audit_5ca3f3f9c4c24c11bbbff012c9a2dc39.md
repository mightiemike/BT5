### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller of `addLiquidity`) and gates access only on `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no requirement that `owner == msg.sender`, any unprivileged caller can bypass the allowlist by naming an already-allowed address as `owner`.

---

### Finding Description

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool" and stores its state in a mapping named `allowedDepositor`. The `beforeAddLiquidity` hook receives two address parameters: `sender` (the actual caller of `addLiquidity` on the pool, i.e. the router or EOA) and `owner` (the position recipient). The implementation drops `sender` entirely and evaluates only `owner`:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the actual swapper):

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [3](#0-2) 

`MetricOmmPool.addLiquidity` imposes no constraint that `owner == msg.sender`: [4](#0-3) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the two-argument `owner` overload) validates only that `owner != address(0)`: [5](#0-4) [6](#0-5) 

**Attack path:**

1. Pool admin deploys a pool with `DepositAllowlistExtension` and whitelists only `alice`.
2. `bob` (not whitelisted) calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, ...)`.
3. The router calls `pool.addLiquidity(owner=alice, ...)` with `msg.sender = router`.
4. `_beforeAddLiquidity(sender=router, owner=alice, ...)` is forwarded to the extension.
5. The extension checks `allowedDepositor[pool][alice]` → `true` → passes.
6. `bob` pays the tokens; the position is minted to `alice`.

The allowlist is completely ineffective: any caller can inject liquidity into a restricted pool by naming any whitelisted address as `owner`.

---

### Impact Explanation

The pool admin's deposit allowlist is rendered a no-op. Any unprivileged address can add liquidity to a pool that is supposed to be restricted to a curated set of depositors. This breaks the admin-boundary invariant: the pool admin's configured guard is bypassed by an unprivileged path without any privileged action or malicious setup. Pools relying on this extension for regulatory compliance, LP curation, or manipulation prevention receive no protection.

---

### Likelihood Explanation

The bypass requires only a standard `addLiquidityExactShares` call with an arbitrary `owner` argument — no special permissions, no flash loans, no oracle manipulation. Any actor who can observe the allowlist (public mapping) can execute this immediately. The `MetricOmmPoolLiquidityAdder` router explicitly exposes the `owner`-parameterized overload to all callers.

---

### Recommendation

Check `sender` (the actual depositor/caller) instead of `owner` (the position recipient), consistent with how `SwapAllowlistExtension` handles `sender`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Also rename the storage mapping from `allowedDepositor` to `allowedSender` (or keep the name but document that it keys on the pool caller, not the position owner) to prevent future confusion.

---

### Proof of Concept

```solidity
// Foundry test — add to metric-periphery/test/extensions/
function test_depositAllowlist_bypass_via_owner_param() public {
    // Setup: pool with DepositAllowlistExtension; only alice is whitelisted
    address alice = makeAddr("alice");
    address bob   = makeAddr("bob");

    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), alice, true);

    // Confirm bob is NOT allowed
    assertFalse(extension.isAllowedToDeposit(address(pool), bob));

    // Bob calls addLiquidity directly on the pool, naming alice as owner
    // The extension checks allowedDepositor[pool][alice] == true → passes
    LiquidityDelta memory delta = LiquidityDelta({
        binIdxs: new int256[](1),
        shares:  new uint256[](1)
    });
    delta.binIdxs[0] = 4;
    delta.shares[0]  = 10_000;

    vm.prank(bob);
    // Succeeds — bob bypasses the allowlist by setting owner = alice
    pool.addLiquidity(alice, 0, delta, abi.encode(uint8(1)), "");
}
```

The call succeeds because `beforeAddLiquidity` receives `sender = bob` (ignored) and `owner = alice` (checked and allowed), so the revert is never triggered. [1](#0-0) [2](#0-1) [7](#0-6) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
