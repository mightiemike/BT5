### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing non-allowlisted actors to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` silently discards the `sender` argument and checks only the position `owner` against the allowlist. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any non-allowlisted actor can route a deposit through `MetricOmmPoolLiquidityAdder` with an allowlisted address as `owner`, passing the guard while the actual payer is never checked.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: `sender` (the pool's `msg.sender`, i.e. the caller who pays) and `owner` (the position recipient). The implementation discards `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool calls the extension with `msg.sender` as the first argument:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (with explicit `owner`) explicitly supports the operator pattern — `msg.sender` pays, but an arbitrary `owner` receives the position:

```solidity
// MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(address pool, address owner, uint80 salt, ...) external payable {
    _validateOwner(owner);   // only rejects address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

The same operator pattern is available through `addLiquidityWeighted(pool, owner, ...)`.

The `SwapAllowlistExtension` — the parallel guard for swaps — correctly checks `sender` (the caller), not the recipient:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry is the root cause: the deposit guard checks the wrong actor.

---

### Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g. KYC-verified market makers, whitelisted protocols) has that restriction fully bypassed. Any non-allowlisted actor can:

1. Identify any allowlisted address `alice`.
2. Call `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, ...)`.
3. The extension checks `allowedDepositor[pool][alice]` → passes.
4. The caller's tokens enter the pool under `alice`'s position key.

The pool receives liquidity from an actor the admin explicitly excluded. The admin-configured access boundary is silently voided. Additionally, the allowlisted owner (`alice`) receives an unsolicited position she did not initiate, which constitutes a griefing vector (forced LP exposure in arbitrary bins).

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` is the canonical periphery entry point and explicitly advertises the `owner ≠ msg.sender` operator pattern (tested in `test_exactShares_canAddOnBehalfOfAnotherOwner`). Any allowlisted address is publicly readable on-chain. The bypass requires no special privilege, no flash loan, and no oracle manipulation — only knowledge of one allowlisted address and willingness to spend tokens.

---

### Recommendation

Mirror the `SwapAllowlistExtension` pattern: check `sender` (the actual depositor/payer), not `owner`:

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

If the intent is instead to restrict who may *hold* a position (owner-gating), the NatDoc and function name must be updated to reflect that, and the bypass via the operator pattern must be explicitly acknowledged as accepted behaviour.

---

### Proof of Concept

```
1. Pool admin deploys pool with DepositAllowlistExtension in beforeAddLiquidity order.
2. Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
   → allowedDepositor[pool][alice] = true; bob is NOT allowlisted.

3. bob calls:
   liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")

4. LiquidityAdder calls pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")
   → pool calls _beforeAddLiquidity(msg.sender=liquidityAdder, owner=alice, ...)

5. Extension evaluates:
   allowedDepositor[pool][alice] == true  →  check passes, no revert.

6. LiquidityLib credits shares to alice's position key.
   LiquidityAdder callback pulls tokens from bob (msg.sender of step 3).

Result: bob's tokens are now in the pool under alice's position.
        The deposit allowlist did not gate bob at any point.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
