Looking at the `DepositAllowlistExtension` hook, I found a clear analog to the M-9 bug class: a guard that checks the wrong address, allowing it to be bypassed.

---

### Title
`DepositAllowlistExtension` checks position `owner` instead of actual `sender`, allowing any unauthorized address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary
The `beforeAddLiquidity` hook in `DepositAllowlistExtension` silently discards the `sender` parameter (the actual caller who pays tokens) and gates on `owner` (the position recipient). Any unprivileged address can bypass the allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` encodes and forwards two distinct addresses to every extension:

- `sender` = `msg.sender` of `pool.addLiquidity(...)` — the address that will be called back and must pay tokens
- `owner` = the position owner argument — the address that will hold the resulting LP shares [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but **discards `sender`** (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

Because `owner` is a free caller-supplied argument to `pool.addLiquidity`, any address can pass the guard by supplying an allowlisted address as `owner`. The pool then issues the callback to the actual `msg.sender` (the unauthorized caller), who pays the tokens: [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` exposes a direct path: the `owner` argument is user-controlled and only validated to be non-zero, while the payer is always `msg.sender` (the unauthorized caller): [4](#0-3) 

The callback settlement pulls tokens from the stored payer (the unauthorized caller), not from `owner`: [5](#0-4) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), confirming the deposit extension's check is the anomaly: [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is an admin-configured guard intended to restrict which addresses may provide liquidity (e.g., KYC/AML compliance, private pool membership, or manipulation prevention). Because the guard checks the position recipient rather than the actual depositor, any unprivileged address can:

1. Add liquidity to a pool they are explicitly excluded from.
2. Manipulate the pool's bin liquidity distribution (affecting LP returns and swap prices for existing LPs) without being subject to the access control the pool admin configured.
3. Undermine regulatory or operational restrictions the pool admin intended to enforce.

The position is credited to the allowlisted `owner`, so the unauthorized caller does not directly profit, but the pool admin's access boundary is fully broken — any actor can deposit into a supposedly restricted pool.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` or `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with `owner` set to any known allowlisted address. The allowlisted addresses are discoverable on-chain via `allowedDepositor` mapping events. Likelihood is **high**.

---

### Recommendation

Change the `beforeAddLiquidity` check to gate on `sender` (the actual depositor/payer) rather than `owner`:

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [2](#0-1) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; only address `A` is allowlisted via `setAllowedToDeposit(pool, A, true)`.
2. Unauthorized address `B` calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, A, salt, deltas, max0, max1, extData)`.
3. The LiquidityAdder calls `pool.addLiquidity(A, salt, deltas, KIND_PAY, extData)` with `msg.sender = LiquidityAdder`.
4. Pool calls `_beforeAddLiquidity(LiquidityAdder, A, ...)` → extension checks `allowedDepositor[pool][A]` = `true` → **passes**.
5. Pool executes liquidity addition, then calls `metricOmmModifyLiquidityCallback` on the LiquidityAdder.
6. LiquidityAdder reads payer = `B` from transient storage and pulls tokens from `B` via `pay(token, B, pool, amount)`.
7. LP position is minted for owner `A`; `B` has paid tokens and bypassed the allowlist entirely.

### Citations

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
