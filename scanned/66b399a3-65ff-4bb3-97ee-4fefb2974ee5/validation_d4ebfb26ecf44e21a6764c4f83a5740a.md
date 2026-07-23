### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position owner) parameter rather than the `sender` (actual caller/payer). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from any `msg.sender`, an unauthorized caller can bypass the allowlist entirely by specifying any allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument and gates on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first parameter (`sender`) is silently discarded. `msg.sender` inside the extension is the pool (the caller of the hook), so the check resolves to: *"is `owner` on the allowlist for this pool?"*

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any external caller:

```solidity
// metric-core/contracts/MetricOmmPool.sol L182-196
function addLiquidity(
    address owner,          // ← caller-supplied, no ownership check
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
``` [2](#0-1) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension. There is no check that `msg.sender == owner` or that `msg.sender` has any authority over `owner`'s position.

The `MetricOmmPoolLiquidityAdder` reinforces the gap: when routing through the adder, `sender` seen by the extension is the `LiquidityAdder` contract address, not the user, so the extension was deliberately written to check `owner`. But this design choice makes the guard trivially bypassable on the direct pool path:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,   // ← any address, only checked != address(0)
    ...
) external payable override returns (...) {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

**Attack path:**

1. Pool admin deploys a curated pool with `DepositAllowlistExtension`; only `Alice` is allowlisted.
2. Unauthorized `Bob` calls `pool.addLiquidity(owner=Alice, salt=X, deltas=..., callbackData=..., extensionData=...)` directly.
3. The extension receives `owner=Alice`, finds her on the allowlist, and returns success.
4. The pool mints shares into Alice's position keyed by `(Alice, X)`.
5. Bob's callback is invoked; Bob pays the tokens.
6. The allowlist is bypassed: Bob deposited into a curated pool without being allowlisted.

The same path works through `MetricOmmPoolLiquidityAdder.addLiquidityWeighted` — the probe call and the paying call both pass because both use `owner=Alice`.

---

### Impact Explanation

The pool admin's curation invariant is broken. Any unprivileged address can deposit into a pool that is supposed to be restricted to specific depositors. Consequences include:

- **Allowlist bypass**: Unauthorized actors deposit into pools intended for KYC'd, protocol-specific, or otherwise curated participants.
- **Unsolicited position creation**: Bob can create positions under Alice's key `(Alice, salt)` for salts Alice never used, adding liquidity to bins Alice did not choose, altering her effective exposure and future withdrawal composition.
- **Pool state manipulation**: An unauthorized depositor controls which bins receive liquidity, shifting the pool's cursor and bin balances in ways that affect all LPs' returns.

This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"* and *"Broken core pool functionality causing loss of funds or unusable liquidity flows."*

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` directly with `owner` set to any allowlisted address. The allowlisted address is discoverable on-chain from `AllowedToDepositSet` events. Likelihood is **High**.

---

### Recommendation

Gate on `sender` (the actual caller/payer), not `owner`. The `SwapAllowlistExtension` already does this correctly:

```solidity
// SwapAllowlistExtension.sol L31-41 — correct pattern
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

Apply the same pattern to `DepositAllowlistExtension`:

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

Because `MetricOmmPoolLiquidityAdder` is the `sender` when routing through the adder, the adder itself must also be allowlisted per pool, or the factory must enforce that the adder forwards the real user identity as `sender`. The simplest fix is to allowlist the `LiquidityAdder` contract and rely on the adder's own `_validateOwner` / payer binding for user-level policy.

---

### Proof of Concept

```solidity
// Assume: pool has DepositAllowlistExtension; only `alice` is allowlisted.
// Bob is NOT allowlisted.

address alice = makeAddr("alice");
address bob   = makeAddr("bob");

// Pool admin allowlists only alice
vm.prank(poolAdmin);
depositAllowlist.setAllowedToDeposit(address(pool), alice, true);

// Bob prepares tokens and approves pool
deal(token0, bob, 1e18);
deal(token1, bob, 1e18);
vm.startPrank(bob);
IERC20(token0).approve(address(pool), type(uint256).max);
IERC20(token1).approve(address(pool), type(uint256).max);

// Bob calls addLiquidity with owner=alice — extension checks alice (allowlisted) → passes
// Bob's metricOmmModifyLiquidityCallback pays the tokens
pool.addLiquidity(
    alice,          // owner — allowlisted, so extension passes
    uint80(999),    // salt alice never used
    deltas,
    abi.encode(bob),  // callback data pointing to bob's payer logic
    ""
);
// Bob successfully deposited into a curated pool without being allowlisted.
// Alice now has an unsolicited position at salt 999.
```

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
