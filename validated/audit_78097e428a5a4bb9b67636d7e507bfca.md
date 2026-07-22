### Title
`DepositAllowlistExtension.beforeAddLiquidity` Guards on `owner` Instead of `sender`, Allowing Any Unauthorized Depositor to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument (the actual caller who pays for the liquidity) and instead validates the `owner` argument (the position beneficiary). Because `owner` is a free caller-supplied parameter in `MetricOmmPool.addLiquidity`, any address not on the allowlist can deposit by nominating an allowlisted address as `owner`, making the guard completely ineffective.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities to the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the address that called `addLiquidity` and will be charged through the liquidity callback.
- `owner` = caller-supplied — the address that will own the resulting position shares.

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (unnamed first parameter) and checks only `owner`: [2](#0-1) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The contract's own NatSpec and setter name confirm the intended subject is the **depositor** (the paying caller), not the position owner: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly uses `sender` (the actual swapper) for its check, confirming the pattern mismatch in the deposit extension: [4](#0-3) 

---

### Impact Explanation

Any address not on the allowlist can call:

```
pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)
```

The extension checks `allowedDepositor[pool][allowlisted_address]` → passes. The unauthorized caller pays for the liquidity through the callback; the allowlisted address receives the position shares. The deposit allowlist is rendered completely inoperative:

1. **Allowlist bypass** — any actor can deposit into a pool configured to restrict deposits to specific addresses.
2. **Griefing** — unauthorized actors can force-create positions in allowlisted users' names without consent; those users must spend gas to remove them.
3. **Pool integrity** — pools relying on the extension for regulatory or access-control purposes have no effective gate.

This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact category.

---

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and the ability to call `pool.addLiquidity`. No special privileges, flash loans, or oracle manipulation are needed. Any actor can execute this in a single transaction.

---

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT allowlisted

Attack:
  // Bob calls addLiquidity with alice as owner
  pool.addLiquidity(
      alice,          // owner — allowlisted, passes the guard
      salt,
      deltas,
      callbackData,   // callback pulls tokens from bob (msg.sender)
      extensionData
  );

Result:
  - Extension checks allowedDepositor[pool][alice] → true → no revert
  - Bob's tokens are pulled via the liquidity callback
  - Alice receives position shares she did not request
  - Bob has deposited into a restricted pool without being on the allowlist
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-20)
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
