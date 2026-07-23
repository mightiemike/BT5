### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the actual token-providing caller) and gates on `owner` (the LP-share recipient) instead. Because `MetricOmmPool.addLiquidity` never enforces `msg.sender == owner`, any non-allowlisted address can name an allowlisted owner and deposit freely, defeating the entire allowlist invariant.

---

### Finding Description

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool."

The pool calls the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

So the hook receives `sender = msg.sender` (the actual depositor who will pay via callback) and `owner` (the position holder who receives shares). The extension implementation is:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first parameter (`sender`) is discarded (unnamed `address,`). Only `owner` is checked. Meanwhile, `addLiquidity` imposes no `msg.sender == owner` constraint:

```solidity
function addLiquidity(
    address owner,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [3](#0-2) 

Token payment is pulled from `msg.sender` (the caller) inside `LiquidityLib.addLiquidity` via the modify-liquidity callback:

```solidity
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
``` [4](#0-3) 

The allowlist check therefore validates the wrong principal: it approves the LP-share recipient while the actual token provider (the real "depositor") is never verified.

---

### Impact Explanation

The deposit allowlist is an admin-configured access-control boundary. Any unprivileged address can bypass it by supplying an allowlisted address as `owner`. The bypassing address:

- Injects tokens into arbitrary bins, manipulating bin balances and the effective price range seen by subsequent swaps.
- Forces the pool to accept liquidity from actors the pool admin explicitly excluded (regulatory, risk, or operational reasons).
- With a colluding allowlisted owner, can recover the deposited tokens via `removeLiquidity` (which only requires `msg.sender == owner`), making the bypass economically free.

This is an admin-boundary break: a factory-configured guard is bypassed by an unprivileged path, satisfying the allowed-impact gate.

---

### Likelihood Explanation

- Requires no special role or privilege — any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- Allowlisted owner addresses are typically discoverable on-chain (emitted in `AllowedToDepositSet` events).
- The bypass is a single direct call; no flash loan or multi-step setup is needed.

---

### Recommendation

Check `sender` (the actual depositor/caller) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the direct pool caller) and ignores `recipient`. [5](#0-4) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is allowlisted; Bob is not.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. `_beforeAddLiquidity(bob, alice, ...)` is dispatched; the extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` pulls tokens from Bob via `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)`.
6. Alice's position is credited with LP shares.
7. Alice calls `removeLiquidity` and returns the tokens to Bob.

Bob has deposited into a restricted pool and recovered his funds — the allowlist provided zero protection.

### Citations

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
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
