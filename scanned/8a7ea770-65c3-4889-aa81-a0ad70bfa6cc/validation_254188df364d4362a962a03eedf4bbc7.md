### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Actual Depositor `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters — `sender` (the actual caller of `addLiquidity`, i.e., the depositor) and `owner` (the LP position recipient) — but silently ignores `sender` and only checks `owner`. Because `owner` is freely chosen by the caller, any unprivileged address can bypass the deposit allowlist by specifying an authorized `owner` address.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

which routes through `ExtensionCalling._beforeAddLiquidity`:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

So the extension receives `sender = msg.sender` (the actual depositor) as its **first** argument and `owner` (the LP position recipient, freely chosen by the caller) as its **second** argument.

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed `address`) and only checks `owner`:

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

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry is the root cause: `SwapAllowlistExtension` gates the actual actor (`sender`); `DepositAllowlistExtension` gates the LP position recipient (`owner`), which the caller controls freely.

---

### Impact Explanation

Any address not in the allowlist can call `pool.addLiquidity(owner = <allowlisted_address>, ...)`. The extension checks `allowedDepositor[pool][allowlisted_address]` → `true` → passes. The unauthorized depositor's funds enter the pool; the LP position is credited to the allowlisted address. The pool admin's intent to restrict deposits to specific addresses (e.g., KYC/AML, institutional-only pools) is completely defeated. Every pool deploying `DepositAllowlistExtension` is affected.

The Smart Audit Pivot explicitly flags this class: *"deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through … owner/salt separation."*

---

### Likelihood Explanation

Triggering requires no special permissions. Any externally-owned account can call `addLiquidity` on any pool that uses `DepositAllowlistExtension`. The authorized owner's address is typically discoverable on-chain (prior deposits, events). No flash loan, callback, or multi-step setup is needed.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both who funds the deposit and who receives the position, check both `sender` and `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address
       salt  = 0,
       deltas = ...,    // bob's tokens
       ...
   );
   ```
3. Pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are deposited; LP position is minted to `alice`.
6. The deposit allowlist provided zero protection against `bob`. [3](#0-2) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-39)
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
```
