### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP position `owner` instead of actual depositor `sender`, allowing any unprivileged address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and validates only the `owner` (LP position recipient). Because `addLiquidity` lets the caller freely choose `owner`, any non-allowlisted address can name an allowlisted address as `owner`, pass the guard, supply tokens through the callback, and deposit into the pool — fully bypassing the admin-configured access control.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-controlled parameter)
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity,
               (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then **discards `sender`** (first argument is unnamed `address`) and only checks `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, ...)   // sender ignored
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

**Attack path:**

1. Pool admin deploys pool with `DepositAllowlistExtension` and allowlists only address `B`.
2. Non-allowlisted address `A` calls `pool.addLiquidity(owner = B, ...)`.
3. The hook evaluates `allowedDepositor[pool][B]` → `true` → no revert.
4. `A` satisfies the token-transfer callback (paying tokens into the pool).
5. The LP position is minted for `B`; `A` has deposited without being on the allowlist.

`removeLiquidity` enforces `msg.sender == owner`, so `A` cannot reclaim the position — but the deposit restriction is fully circumvented: `A` has altered pool bin balances and bin totals from an unpermissioned path. [4](#0-3) 

The contract's own NatSpec says *"Gates `addLiquidity` by depositor address"* — the depositor is `sender`, not `owner`.

---

### Impact Explanation

The admin-configured deposit guard is bypassed by any unprivileged caller. Consequences:

- **Access-control invariant broken**: the pool admin cannot enforce who supplies liquidity; regulatory or risk-management allowlists are rendered ineffective.
- **Bin-state manipulation**: an unpermissioned actor can shift bin balances and `binTotals`, affecting per-share metrics used by `OracleValueStopLossExtension` watermarks and altering the liquidity profile seen by all swappers.
- **Griefing of allowlisted LPs**: `B` receives unsolicited LP positions; while `B` can remove them, the pool state has already been modified.

Meets the **admin-boundary break** gate: *"factory/oracle role checks are bypassed by an unprivileged path."*

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with a known allowlisted address as `owner`. The allowlisted address is discoverable on-chain via `allowedDepositor` public mapping. Likelihood is **high**.

---

### Recommendation

Check `sender` (the actual token provider) instead of `owner` (the position recipient):

```solidity
// DepositAllowlistExtension.sol
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

```solidity
// Assume: pool configured with DepositAllowlistExtension
// Pool admin has called: ext.setAllowedToDeposit(pool, allowedLP, true)
// attacker is NOT on the allowlist

address allowedLP = 0xABCD...;   // allowlisted
address attacker  = 0x1234...;   // NOT allowlisted

// attacker calls addLiquidity naming allowedLP as owner
// beforeAddLiquidity checks allowedDepositor[pool][allowedLP] == true → passes
// attacker's callback transfers tokens into the pool
// LP position minted for allowedLP
// attacker has deposited without allowlist approval
pool.addLiquidity(
    allowedLP,          // owner  ← allowlisted, check passes
    salt,
    deltas,
    callbackData,       // attacker's contract pays tokens here
    extensionData
);
// pool.binTotals now reflects attacker's deposit; allowlist guard defeated
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
