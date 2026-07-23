### Title
`DepositAllowlistExtension` gates position `owner` instead of the actual depositor `sender`, allowing any unprivileged caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual payer/caller) and checks only `owner` (the LP position recipient). Because `MetricOmmPoolLiquidityAdder` lets any caller freely specify an arbitrary `owner`, a non-allowlisted user can deposit into a curated pool by naming an allowlisted address as the position owner, paying the tokens themselves, and receiving the LP position back through the allowlisted address.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared with the `sender` parameter explicitly unnamed (discarded):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool calls `_beforeAddLiquidity(msg.sender /*=caller of addLiquidity*/, owner, ...)` and forwards both to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 88-99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-controlled `owner` parameter and validates only that it is non-zero:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol  line 56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

`_addLiquidity` then calls `pool.addLiquidity(positionOwner, ...)` where `positionOwner` is the attacker-supplied `owner`: [4](#0-3) 

The pool receives `msg.sender = liquidityAdder` as `sender` and the attacker-supplied address as `owner`. The extension ignores `sender` and checks only `allowedDepositor[pool][owner]`. If the attacker supplies any allowlisted address as `owner`, the check passes unconditionally.

---

### Impact Explanation

A non-allowlisted user can deposit into a curated pool by:
1. Calling `addLiquidityExactShares(pool, allowlistedUser, ...)` on the liquidity adder.
2. The extension sees `owner = allowlistedUser` (allowlisted) and passes.
3. The non-allowlisted user's tokens are pulled; the LP position is minted to `allowlistedUser`.

The allowlisted user can then remove the liquidity and return the proceeds. This completely defeats the deposit allowlist: the pool admin's configured access-control boundary is bypassed by any unprivileged caller without any admin action or privileged setup. The invariant "only allowlisted depositors may mint LP shares" is broken.

---

### Likelihood Explanation

The attack path is fully permissionless. Any user can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an arbitrary `owner`. No special role, no admin cooperation, and no non-standard token behavior is required. The only prerequisite is knowing one allowlisted address, which is on-chain readable via `allowedDepositor` or `AllowedToDepositSet` events.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner`:

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

Rename the mapping and setter to `allowedSender` / `setAllowedToDeposit(pool, sender, allowed)` to make the intent unambiguous. Update `isAllowedToDeposit` accordingly.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `allowedUser` is allowlisted.
// attacker is NOT allowlisted.

// Step 1: attacker calls the liquidity adder with allowedUser as owner
liquidityAdder.addLiquidityExactShares(
    pool,
    allowedUser,   // <-- allowlisted address, not the attacker
    salt,
    deltas,
    maxAmount0,
    maxAmount1,
    ""
);
// Extension checks allowedDepositor[pool][allowedUser] == true → passes.
// Attacker's tokens are pulled; LP shares minted to allowedUser.

// Step 2: allowedUser removes liquidity and returns tokens to attacker off-chain.
// Net result: attacker deposited into a curated pool with zero allowlist enforcement.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```
