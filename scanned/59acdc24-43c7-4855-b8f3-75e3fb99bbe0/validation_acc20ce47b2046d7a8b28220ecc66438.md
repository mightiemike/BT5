### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter (the actual caller of `pool.addLiquidity`) and checks only the `owner` parameter (the position owner). Because `MetricOmmPoolLiquidityAdder` lets any caller supply an arbitrary `owner`, a non-allowlisted actor can pass an allowlisted address as `owner` and deposit freely, bypassing the curated-pool gate entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two identity values to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the direct caller of `addLiquidity` (the LiquidityAdder when routed through periphery); `owner` is the position-owner address supplied by that caller.

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` and checks only `owner`:

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

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` accepts a caller-supplied `owner` and validates it only for non-zero:

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

The actual payer is `msg.sender` of the LiquidityAdder call, stored separately in transient context. The pool call becomes:

```solidity
IMetricOmmPoolActions(pool).addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData)
```

where `positionOwner` = caller-supplied `owner` (e.g., Alice), and the payer is Bob. The extension sees `owner = alice` (allowlisted) and passes. Bob's tokens are pulled in the callback and deposited into Alice's position. Bob never appears in the allowlist check.

The same path exists through `addLiquidityWeighted(pool, owner, ...)`.

---

### Impact Explanation

The deposit allowlist is the primary access-control boundary for curated pools. Bypassing it allows:

1. **Unauthorized deposit**: A non-allowlisted actor deposits tokens into the pool without the pool admin's approval, breaking the curated-pool invariant.
2. **Unsolicited position creation**: The non-allowlisted actor mints LP shares into any allowlisted address's position without that address's consent, which is a griefing vector (the victim now holds an LP position they did not initiate and must actively remove).
3. **Admin-boundary break**: The pool admin's configured allowlist is rendered ineffective for any caller who routes through the public `MetricOmmPoolLiquidityAdder`, which is the documented and supported periphery path for liquidity provision.

---

### Likelihood Explanation

`MetricOmmPoolLiquidityAdder` is a public, permissionless contract. Any actor can call `addLiquidityExactShares` or `addLiquidityWeighted` with an arbitrary `owner`. The only prerequisite is that at least one address is already allowlisted (a normal operational state for any curated pool). No privileged access, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should gate on `sender` — the address that actually initiates the deposit — rather than `owner`. When the LiquidityAdder is the supported entry point, `sender` is the LiquidityAdder itself, so the extension should additionally check the original `msg.sender` of the periphery call. One approach: check both `sender` and `owner`, or require the pool to forward the original initiator as `sender` rather than `msg.sender` of `addLiquidity`.

```solidity
// Current (broken): checks owner, ignores sender
function beforeAddLiquidity(address /*sender*/, address owner, ...)

// Fixed: check sender (the actual depositing actor)
function beforeAddLiquidity(address sender, address /*owner*/, ...)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Pool admin allowlists Alice: `setAllowedToDeposit(pool, alice, true)`.
3. Bob (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "");
   ```
4. LiquidityAdder calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")`.
5. Pool calls `_beforeAddLiquidity(address(liquidityAdder), alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][alice]` → `true` → hook passes.
7. Pool mints LP shares credited to Alice; callback pulls tokens from Bob (the transient payer).
8. Bob has deposited into the pool without being allowlisted. Alice holds an LP position she never initiated.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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
