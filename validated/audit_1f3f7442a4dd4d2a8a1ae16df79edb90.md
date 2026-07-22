### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual caller of `addLiquidity`) and instead gates on `owner` (the LP-position beneficiary). Because `owner` is a caller-controlled argument, any address not on the allowlist can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

The `IMetricOmmExtensions.beforeAddLiquidity` interface passes two distinct addresses:

- `sender` — `msg.sender` of the pool's `addLiquidity` call (the actual depositor who pays tokens via callback)
- `owner` — the LP-position beneficiary supplied by the caller as an argument [1](#0-0) 

The pool passes them in that order: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` leaves the first parameter (`sender`) unnamed and therefore unused, then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The extension's own NatSpec declares its purpose as "Gates `addLiquidity` by depositor address, per pool." [4](#0-3) 

The `allowedDepositor` mapping name reinforces that the intended subject is the depositing address, not the position owner. [5](#0-4) 

Because `owner` is a free argument supplied by the caller to `addLiquidity`, any address can pass an allowlisted address as `owner` while remaining the actual token-paying `sender`. [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective. Any address — regardless of allowlist status — can call `pool.addLiquidity(allowlistedAddress, ...)`, pass the guard, and inject tokens into the pool. The pool admin's access-control intent is silently nullified. This breaks the core liquidity-gating invariant the extension is deployed to enforce.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can trigger it in a single transaction by supplying any allowlisted address as `owner`. The bypass is unconditional whenever the extension is configured on a pool.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which names and checks `sender` while leaving `recipient` unnamed: [7](#0-6) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured.
2. Pool admin allowlists only `Alice`; `Charlie` is not allowlisted.
3. `Charlie` calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity` receives `sender = Charlie`, `owner = Alice`.
5. The guard evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `Charlie`'s callback is invoked; `Charlie` pays the tokens; `Alice` receives the LP shares.
7. `Charlie` has deposited into a pool that was supposed to block him, with zero on-chain friction. [3](#0-2)

### Citations

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-12)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
