### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Depositors to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument and instead validates the `owner` (LP position recipient) against the allowlist. Because `addLiquidity` lets any `msg.sender` supply an arbitrary `owner`, any non-allowlisted address can bypass the guard by naming an allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied, arbitrary)
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension verbatim: [2](#0-1) 

The extension's hook signature receives `sender` as its first positional argument, but the implementation **names it `_` (discards it)** and checks `owner` instead: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`sender` — the address that actually calls `addLiquidity`, triggers the token-transfer callback, and is the entity the allowlist is meant to restrict — is never read. `owner` — an arbitrary address chosen by the caller — is what gets validated.

The `SwapAllowlistExtension` does **not** share this flaw; it correctly reads `sender`: [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective as an access-control gate:

1. **Allowlist bypass** — Any non-allowlisted address can deposit into a restricted pool by specifying any allowlisted address as `owner`. The non-allowlisted address supplies tokens via the `IMetricOmmSwapCallback` callback; the check passes because `owner` is allowlisted.
2. **Compliance / KYC failure** — Pools relying on this extension for regulatory gating (e.g., permissioned liquidity pools) silently admit unchecked depositors.
3. **Griefing** — A non-allowlisted attacker can force LP positions onto allowlisted addresses without their consent, potentially locking their capital or creating unwanted exposure.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any externally-owned account can call `addLiquidity` with `owner` set to any allowlisted address. The attack is a single transaction with no preconditions beyond knowing one allowlisted address (which is readable from `allowedDepositor` or emitted events).

---

### Recommendation

Rename the first parameter from `_` to `sender` and check it instead of `owner`:

```solidity
// Before (buggy)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook. `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, Bob, true)`. Alice is **not** allowlisted.
3. Alice calls `pool.addLiquidity(owner = Bob, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(sender = Alice, owner = Bob, ...)`.
5. Extension evaluates `allowedDepositor[pool][Bob]` → `true` → **no revert**.
6. `LiquidityLib.addLiquidity` executes; Alice's `metricOmmSwapCallback` is invoked and Alice transfers tokens into the pool.
7. The LP position is minted under `(Bob, salt)`.
8. Alice — a non-allowlisted address — has successfully deposited into a pool whose allowlist was supposed to block her. The guard is fully bypassed. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-42)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
