### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the LP-position `owner` parameter against the allowlist instead of the actual depositor (`sender`). Because `owner` is a free caller-supplied argument to `pool.addLiquidity()`, any unauthorized address can pass an allowlisted address as `owner` and the guard passes unconditionally, defeating the entire deposit restriction.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards two distinct addresses to the extension hook:

- `sender` = `msg.sender` — the address that actually calls the pool and pays tokens via the swap-callback mechanism.
- `owner` = a caller-supplied argument — the address that will own the resulting LP-position shares. [1](#0-0) 

The pool passes both to `_beforeAddLiquidity`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed) parameter and `owner` as its second, but the guard only inspects `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is `sender`, not `owner`. Because `owner` is a free parameter supplied by the caller, any address can pass an allowlisted address as `owner` and the check trivially passes.

---

### Impact Explanation

Any unauthorized address can add liquidity to a permissioned pool by calling:

```
pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)
```

The extension sees `allowedDepositor[pool][allowlisted_address] == true` and does not revert. The unauthorized caller pays the tokens (via the pool's callback to `msg.sender`) and the allowlisted address receives LP shares it never requested. The deposit allowlist — the sole access-control mechanism for liquidity provisioning — is rendered inoperative. Any actor can force liquidity into specific bins, altering pool depth and price-impact characteristics in ways the pool admin explicitly prohibited.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity` with an arbitrary `owner`. The allowlisted addresses are discoverable on-chain via the `AllowedToDepositSet` events. Likelihood is **High**.

---

### Recommendation

Replace the `owner` check with a `sender` check so the guard validates the actual depositing address:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only Alice:
   ```
   extension.setAllowedToDeposit(pool, alice, true);
   ```
2. Bob (not allowlisted) constructs a `callbackData` that causes the pool's callback to pull tokens from Bob's own balance.
3. Bob calls:
   ```
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
4. The pool invokes `extension.beforeAddLiquidity(bob, alice, ...)`.
5. The guard evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are transferred into the pool; Alice receives LP shares.
7. Bob has successfully deposited into a pool he is not authorized to touch, and can repeat this for any bin configuration, altering pool liquidity depth without restriction. [4](#0-3) [5](#0-4)

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
