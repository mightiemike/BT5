### Title
`DepositAllowlistExtension` Gates Position Owner Instead of Actual Payer, Allowing Non-Allowlisted Actors to Bypass Deposit Guard via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` (position owner) argument instead of the `sender` (actual caller of `addLiquidity`). When deposits flow through `MetricOmmPoolLiquidityAdder`, the pool passes `sender = LiquidityAdder` and `owner = caller-specified`. A non-allowlisted actor can bypass the deposit gate entirely by naming any allowlisted address as the position owner.

---

### Finding Description

**Identity mismatch in the allowlist check**

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to the extension: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and gates only on `owner`: [3](#0-2) 

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

**How `MetricOmmPoolLiquidityAdder` separates payer from owner**

`addLiquidityExactShares(pool, owner, salt, ...)` explicitly accepts an arbitrary `owner` while always using `msg.sender` as the payer: [4](#0-3) 

The internal `_addLiquidity` call then invokes `pool.addLiquidity(positionOwner, ...)` with `msg.sender = LiquidityAdder`: [5](#0-4) 

So the pool sees:
- `sender = LiquidityAdder` (the contract, not the real user)
- `owner = attacker-supplied address`

The extension ignores `sender` and checks only `owner`. If the attacker supplies any allowlisted address as `owner`, the check passes unconditionally.

---

### Impact Explanation

A non-allowlisted actor (Bob) can:

1. Call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, ...)` where `alice` is an allowlisted address.
2. The pool calls `beforeAddLiquidity(sender=LiquidityAdder, owner=alice, ...)`.
3. The extension checks `allowedDepositor[pool][alice]` → alice is allowlisted → **passes**.
4. Bob's tokens are pulled from Bob and deposited into the pool; alice receives LP shares she never requested.

Consequences:
- **Deposit allowlist bypass**: Non-allowlisted actors inject tokens into a permissioned pool, violating the admin-configured compliance gate.
- **Griefing of allowlisted LPs**: Allowlisted addresses receive unwanted LP positions. Removing them requires an additional transaction and exposes the victim to pool-state risk during the window.
- **Admin-boundary break**: The pool admin's intent to restrict depositors to a known set is fully circumvented by any unprivileged caller who knows one allowlisted address (which is public on-chain via `AllowedToDepositSet` events).

---

### Likelihood Explanation

- The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract.
- Allowlisted addresses are discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
- No special role, flash loan, or multi-block setup is required; a single transaction suffices.
- Any pool that deploys `DepositAllowlistExtension` to gate depositors is affected.

---

### Recommendation

Gate on `sender` (the actual caller of `addLiquidity`) rather than `owner` (the position owner), since `sender` is the address that pays tokens and initiates the deposit:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
