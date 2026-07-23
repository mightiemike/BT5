### Title
`DepositAllowlistExtension` checks position `owner` instead of actual depositor `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the position beneficiary) against the allowlist instead of the `sender` parameter (the actual depositor/payer). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address supplied by the caller, any unauthorized address can bypass the deposit guard entirely by setting `owner` to any allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary to the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` then encodes both and forwards them to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter, named `_`) and checks `owner` instead: [3](#0-2) 

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

The contract's own NatSpec states its purpose is to **"Gate `addLiquidity` by depositor address, per pool."** The depositor — the address that pays tokens via the modify-liquidity callback — is `sender` (`msg.sender` of the pool call), not `owner`. By checking `owner`, the guard is applied to the wrong identity.

The inconsistency is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`: [4](#0-3) 

```solidity
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The deposit extension is structurally identical to the swap extension except it checks the wrong parameter.

---

### Impact Explanation

The deposit allowlist guard is completely ineffective. Any address — regardless of allowlist status — can deposit into a restricted pool by calling:

```solidity
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension checks `allowedDepositor[pool][allowlistedAddress]`, which passes. The unauthorized caller pays the tokens via the callback; the position is attributed to `allowlistedAddress`. Consequences:

1. **Broken access control**: Pool admins cannot restrict who deposits into their pool. Any private or permissioned pool using this extension has no effective deposit gate.
2. **Forced positions on allowlisted addresses**: An attacker can force unexpected bin positions onto any allowlisted address, which the victim did not authorize and may not be able to anticipate.
3. **Unauthorized pool state manipulation**: An attacker can add liquidity at arbitrary bins in a restricted pool, altering the bin balance distribution and affecting swap execution for all users.

---

### Likelihood Explanation

Exploitation requires no special privileges. Allowlisted addresses are publicly readable via `allowedDepositor` and `allowAllDepositors`. Any address that can call `addLiquidity` on the pool (i.e., any EOA or contract) can execute the bypass. The attack is a single transaction with no preconditions beyond knowing one allowlisted address.

---

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner` (the position beneficiary):

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` wired to `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob`'s tokens are pulled via the swap callback; the position is recorded under `alice`.
6. `bob` has deposited into a restricted pool, bypassing the allowlist entirely.
7. `alice` now holds an unexpected position she did not create; `bob` has manipulated the pool's bin state without authorization.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
