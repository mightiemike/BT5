The vulnerability is real. Let me trace the exact execution path before writing up the finding.

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing any disallowed address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner` (the position owner) instead. Because `owner` is a free caller-supplied parameter with no access restriction, any disallowed address can bypass the allowlist by naming an allowed address as the position owner.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: `sender` (the direct caller of the pool, i.e. `msg.sender` at the pool level) and `owner` (the position owner chosen by the caller). The function signature discards `sender` entirely and evaluates only `owner`:

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
``` [1](#0-0) 

`owner` is a free parameter: `MetricOmmPool.addLiquidity` accepts any `owner` address from any caller without restriction. [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly allows the caller to supply an arbitrary `owner` distinct from `msg.sender`, and records `msg.sender` as the payer in transient storage — not `owner`. [3](#0-2) 

The payer (token source) is stored separately and used only in the callback: [4](#0-3) 

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool."* The mapping is named `allowedDepositor`, not `allowedOwner`. The check should be on `sender` (the actual depositing party), not `owner`. [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any disallowed address can deposit tokens into a restricted pool by specifying any allowed address as `owner`. The disallowed address pays the tokens; the allowed address receives the shares. The pool admin's intent to restrict who may deposit is fully circumvented. This breaks the core access-control functionality of `DepositAllowlistExtension` and any pool that relies on it for KYC, compliance, or permissioned liquidity provisioning.

---

### Likelihood Explanation

The bypass requires only knowledge of one allowed address (publicly readable from `allowedDepositor`) and a single call to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with `owner` set to that address. No privileged access, no special token behavior, and no off-chain data are needed. Any disallowed address can execute this immediately.

---

### Recommendation

Check `sender` (the direct pool caller) instead of `owner` in `beforeAddLiquidity`:

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

Note that when `MetricOmmPoolLiquidityAdder` is used, `sender` will be the adder contract, not the end-user payer. If end-user gating through the adder is also required, the adder must forward the original `msg.sender` via `extensionData`, and the extension must decode and check it.

---

### Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` as `extension1`.
2. Pool admin calls `setAllowedToDeposit(pool, B, true)` — only address B is allowed.
3. Address A (not in the allowlist) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(pool, /*owner=*/B, salt, deltas, max0, max1, "");
   ```
4. Execution path:
   - `LiquidityAdder` stores `payer = A` in transient storage.
   - `pool.addLiquidity(owner=B, ...)` is called with `msg.sender = LiquidityAdder`.
   - Pool calls `extension.beforeAddLiquidity(sender=LiquidityAdder, owner=B, ...)`.
   - Extension evaluates `allowedDepositor[pool][B]` → `true` → **no revert**.
   - Pool calls `LiquidityAdder.metricOmmModifyLiquidityCallback(...)`.
   - Adder pulls tokens from A (the stored payer) and sends them to the pool.
   - Shares are minted under position `(B, salt)`.
5. Assert: A's token balance decreased, B holds new shares, and the deposit succeeded despite A being disallowed.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-191)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```
