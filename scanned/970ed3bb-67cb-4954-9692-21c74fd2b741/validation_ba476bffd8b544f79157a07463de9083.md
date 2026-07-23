### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives two distinct actor addresses — `sender` (the `msg.sender` of the `addLiquidity` call, i.e., the actual token provider) and `owner` (the LP position owner, a caller-controlled parameter). The hook silently discards `sender` and enforces the allowlist only against `owner`. Because `owner` is a free parameter that any caller can set to any allowlisted address, the guard is trivially bypassed: an unauthorized depositor sets `owner` to any allowlisted address, the check passes, and the unauthorized party's tokens enter the pool.

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` and `owner` to the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

```solidity
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, ...))
    );
}
```

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and ignored. The allowlist check is performed only on `owner`: [3](#0-2) 

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

`owner` is a caller-supplied parameter with no on-chain binding to the actual token provider. The pool enforces `msg.sender == owner` only in `removeLiquidity`: [4](#0-3) 

No equivalent constraint exists in `addLiquidity`.

The `isAllowedToDeposit` view function names its second parameter `depositor`, confirming the design intent was to gate the actual depositing party: [5](#0-4) 

### Impact Explanation

Any address — regardless of allowlist status — can deposit tokens into a restricted pool by calling `pool.addLiquidity(owner = <any_allowlisted_address>, ...)`. The unauthorized caller provides tokens via the swap callback (on `msg.sender`), while the allowlisted address receives the LP shares. The deposit allowlist guard is completely ineffective: the pool admin's intent to restrict liquidity providers is broken for every pool using this extension.

Secondary impact: the allowlisted `owner` receives LP shares they did not initiate, exposing them to unwanted impermanent loss or pool-state risk without consent.

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct storage reads) and the ability to call `addLiquidity` with a crafted `owner` parameter. No privileged access, flash loan, or special token behavior is needed. Any external actor can trigger this at any time.

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

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

This aligns the check with the actual token provider (`msg.sender` of `addLiquidity`) rather than the freely-chosen LP position owner.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`. Admin calls `setAllowedToDeposit(pool, Alice, true)`.
2. Bob (not allowlisted) constructs a contract that implements the add-liquidity callback and holds tokens.
3. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice] == true` → check passes.
6. Pool executes liquidity addition: Bob's callback transfers tokens into the pool; Alice receives LP shares.
7. Bob has deposited into a pool he was explicitly excluded from. The allowlist is bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
