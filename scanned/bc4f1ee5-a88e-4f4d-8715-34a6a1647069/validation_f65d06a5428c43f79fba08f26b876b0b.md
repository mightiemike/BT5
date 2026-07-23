### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unauthorized depositor to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is configured as a `beforeAddLiquidity` hook to gate deposits by depositor address. The hook receives both `sender` (the actual caller/depositor) and `owner` (the LP position recipient) as arguments, but only checks `owner`. The `sender` argument is silently discarded. Any unauthorized address can bypass the allowlist by calling `addLiquidity` with an allowlisted `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an `owner` parameter (who receives LP shares) that is independent of `msg.sender` (the actual depositor who provides tokens via callback). The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both actors to the extension. [1](#0-0) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first argument (`sender`) is unnamed and unused. The guard only checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The contract's own NatSpec states it "Gates `addLiquidity` by depositor address, per pool." The depositor is `sender` (`msg.sender` of `addLiquidity`), not `owner`. The `sender` parameter is accepted by the hook signature but never evaluated — a direct structural analog to the external report where `internalBalances` is accepted and verified in a merkle leaf but never credited. [3](#0-2) 

---

### Impact Explanation

The deposit allowlist guard is completely bypassed for the actual depositor. Any address not on the allowlist can call `pool.addLiquidity(owner = allowlistedAddress, ...)`, pass the extension check (because `owner` is allowlisted), provide tokens via the swap callback, and successfully add liquidity to a pool that was intended to be restricted. The allowlist invariant — that only approved depositors can provide liquidity — is broken for every pool using this extension.

---

### Likelihood Explanation

The bypass requires no special privilege. Any external address can call `addLiquidity` directly on the pool with an `owner` set to any allowlisted address (e.g., the pool admin, a known LP, or any address the attacker can observe on-chain). The allowlisted `owner` need not cooperate. The attacker only needs to supply the tokens via the callback, which they control. This is trivially reachable on any pool that has `DepositAllowlistExtension` wired into `BEFORE_ADD_LIQUIDITY_ORDER`. [4](#0-3) 

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as extension 1, `BEFORE_ADD_LIQUIDITY_ORDER = 1`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(sender = bob, owner = alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true`. No revert.
6. Bob's callback executes, Bob transfers tokens into the pool.
7. Alice receives LP shares she did not request; Bob has successfully deposited into an allowlist-protected pool without being on the allowlist. [2](#0-1) [1](#0-0)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-12)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
