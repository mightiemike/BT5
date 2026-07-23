### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` argument (the LP-position recipient) rather than the `sender` argument (the actual `msg.sender` of `addLiquidity`). Because `owner` is a free caller-controlled parameter, any unpermissioned address can bypass the allowlist by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension as positional arguments `(sender, owner, ...)`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and enforces the allowlist only against `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

`owner` is a free parameter supplied by the caller of `addLiquidity`; it is never verified to equal `msg.sender` anywhere in the pool. An unpermissioned address therefore passes the guard by setting `owner` to any address that is already on the allowlist.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly reads the first positional argument (`sender`) and ignores the second (`recipient`): [4](#0-3) 

The two extensions are structurally symmetric, but the deposit variant checks the wrong slot.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity. With this bug the guard is entirely inoperative: any address can call `addLiquidity(owner = <allowlisted_address>, ...)`, satisfy the check, and inject tokens into the pool. LP shares are minted to the allowlisted `owner`; the caller's tokens are irrevocably transferred to the pool via the swap callback. The pool admin's configured access boundary is silently bypassed on every deposit made by an unpermissioned caller.

---

### Likelihood Explanation

Exploitation requires only a single `addLiquidity` call with a publicly observable allowlisted address as `owner`. No privileged access, flash loan, or multi-step setup is needed. Any allowlisted address is discoverable from on-chain `AllowedToDepositSet` events. Likelihood is **High**.

---

### Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist against it, mirroring `SwapAllowlistExtension`:

```diff
-function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
     external view override returns (bytes4)
 {
-    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
         revert IMetricOmmPoolActions.NotAllowedToDeposit();
     }
``` [3](#0-2) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. `bob` (not allowlisted) observes `alice`'s address on-chain.
3. `bob` calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., ...)`.
4. Inside `beforeAddLiquidity`: `allowedDepositor[pool][alice] == true` → no revert.
5. `LiquidityLib.addLiquidity` mints LP shares to `alice`; the pool's swap callback pulls tokens from `bob`.
6. `bob` has deposited into a pool he was explicitly barred from; the allowlist produced zero protection. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
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
