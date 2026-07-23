### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by the **depositor** address. However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, who provides tokens via callback) and instead validates `owner` (the caller-supplied position recipient). Because `owner` is a free argument any caller can set to any address, an unauthorized depositor can bypass the allowlist entirely by naming any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Inside `ExtensionCalling._beforeAddLiquidity`, both `sender` (`msg.sender`) and `owner` (caller-supplied) are forwarded to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

The `IMetricOmmExtensions` interface explicitly names both parameters:

```solidity
function beforeAddLiquidity(
    address sender,
    address owner,
    ...
) external returns (bytes4);
``` [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [4](#0-3) 

The extension's own NatSpec and admin API name the gated entity a **depositor** — the entity that provides tokens:

```solidity
/// @notice Gates `addLiquidity` by depositor address, per pool.
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external ...
``` [5](#0-4) 

The actual token provider is `sender` (`msg.sender` of `addLiquidity`), not `owner`. `owner` is a free argument any caller can set to any address, including addresses the pool admin has explicitly allowlisted.

---

### Impact Explanation

Any address — regardless of allowlist status — can call:

```
pool.addLiquidity(owner = <any_allowlisted_address>, salt, deltas, callbackData, extensionData)
```

The hook checks `allowedDepositor[pool][allowlisted_address]` → passes. The unauthorized caller provides tokens via the `IMetricOmmModifyLiquidityCallback` and the position is minted to the allowlisted address. The deposit allowlist is completely defeated: restricted pools intended for KYC'd, whitelisted, or protocol-controlled depositors accept liquidity from any arbitrary address. Additionally, the allowlisted address receives an unsolicited LP position it did not authorize, which exposes it to pool-side losses (impermanent loss, fee drag) until it manually removes the position.

---

### Likelihood Explanation

The `addLiquidity` function is permissionless — any EOA or contract can call it. The bypass requires only setting `owner` to any address that the pool admin has allowlisted (e.g., the pool admin themselves, a known LP, or any address visible on-chain via past `AllowedToDepositSet` events). No privileged access, no special tokens, and no complex setup are required. Any pool that deploys `DepositAllowlistExtension` without `allowAllDepositors = true` is immediately vulnerable.

---

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
``` [4](#0-3) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowed.
3. Bob (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address
       salt  = 0,
       deltas = <valid bins/shares>,
       callbackData = ...,
       extensionData = ""
   );
   ```
4. `beforeAddLiquidity` receives `sender=Bob, owner=Alice`. It checks `allowedDepositor[pool][alice]` → `true`. No revert.
5. Bob's callback pays the tokens. The pool mints shares to `(alice, 0)`.
6. Bob has bypassed the allowlist: he deposited into a restricted pool, and Alice now holds an unwanted LP position she did not initiate. [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-21)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
